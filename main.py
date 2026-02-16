"""
Main entry point — scheduler loop that scans markets and sends signals.
v4: Auto-trading via Bybit API + exit tracking + marathon.
"""

import asyncio
import logging
import signal as os_signal
import sys
from datetime import datetime, timezone, timedelta
from typing import Optional

import config
from data_fetcher import DataFetcher
from signal_engine import SignalEngine, Signal
from telegram_bot import TelegramSignalBot
from exit_tracker import ExitTracker, TrackedPosition
from marathon import MarathonTracker
from trader import BybitTrader

# ── Logging Setup ──────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format="%(asctime)s │ %(levelname)-7s │ %(name)-18s │ %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def signal_to_tracked(signal: Signal) -> TrackedPosition:
    """Convert a Signal into a TrackedPosition for exit tracking."""
    return TrackedPosition(
        symbol=signal.symbol,
        direction=signal.direction.value,  # "LONG" or "SHORT"
        entry_price=signal.current_price,
        stop_loss=signal.stop_loss,
        take_profit_1=signal.take_profit_1,
        take_profit_2=signal.take_profit_2,
        score=signal.score,
        position_size_pct=signal.position_size_pct,
    )


# Trade cooldown tracker (symbol -> last trade time)
_trade_cooldowns: dict[str, datetime] = {}


async def scan_loop(
    engine: SignalEngine,
    bot: TelegramSignalBot,
    tracker: ExitTracker,
    trader: Optional[BybitTrader] = None,
):
    """Main scan loop — runs every SCAN_INTERVAL_MINUTES."""
    while True:
        try:
            now = datetime.now(timezone.utc)
            logger.info("═" * 50)
            logger.info(f"🔍 Scan cycle started at {now.strftime('%H:%M:%S')} UTC")
            logger.info(f"   Pairs: {len(config.TRADING_PAIRS)} | Threshold: {config.SIGNAL_THRESHOLD}/100")
            if trader:
                logger.info(f"   💱 Auto-trade: ON | Open: {len(trader.get_open_positions())}/{config.MAX_OPEN_POSITIONS}")
            logger.info("═" * 50)

            signals = engine.scan_all()

            if signals:
                sent = await bot.send_signals(signals)
                logger.info(f"📤 Sent {sent}/{len(signals)} signals")

                for signal in signals:
                    # Track for exit management
                    position = signal_to_tracked(signal)
                    tracker.add_position(position)

                    # Auto-trade if enabled
                    if trader and config.AUTO_TRADE_ENABLED:
                        open_count = len(trader.get_open_positions())
                        if open_count >= config.MAX_OPEN_POSITIONS:
                            logger.info(
                                f"⚠️ Max positions ({config.MAX_OPEN_POSITIONS}) reached, "
                                f"skipping {signal.symbol}"
                            )
                            await bot.send_status_message(
                                f"⚠️ *Макс позицій ({config.MAX_OPEN_POSITIONS})* — "
                                f"{signal.symbol} пропущено"
                            )
                            continue

                        # Guard: skip if already in a position for this symbol
                        if trader.get_position_for_symbol(signal.symbol):
                            logger.info(
                                f"⏭ {signal.symbol} — позиція вже відкрита, пропускаю"
                            )
                            continue

                        # Cooldown: skip if traded this symbol recently
                        now_ts = datetime.now(timezone.utc)
                        if signal.symbol in _trade_cooldowns:
                            elapsed = now_ts - _trade_cooldowns[signal.symbol]
                            if elapsed < timedelta(minutes=config.SIGNAL_COOLDOWN_MINUTES):
                                logger.info(
                                    f"⏭ {signal.symbol} — trade cooldown "
                                    f"({elapsed.seconds // 60}m / {config.SIGNAL_COOLDOWN_MINUTES}m)"
                                )
                                continue

                        result = trader.open_position(signal)
                        if result:
                            _trade_cooldowns[signal.symbol] = datetime.now(timezone.utc)
                            await bot.send_status_message(
                                f"💱 *ОРДЕР ВИКОНАНО*\n\n"
                                f"📊 {result['symbol']} {result['side'].upper()}\n"
                                f"💰 Ціна: `{result['fill_price']:,.2f}`\n"
                                f"📏 Кількість: `{result['amount']}`\n"
                                f"💼 Розмір: `${result['position_usd']:.2f}`\n"
                                f"⚡ Плече: {result['leverage']}x\n"
                                f"🛑 SL: `{result['sl']:,.2f}`\n"
                                f"🎯 TP: `{result['tp2']:,.2f}`"
                            )
                        else:
                            await bot.send_status_message(
                                f"❌ Не вдалося відкрити {signal.symbol}"
                            )
            else:
                logger.info("😴 No signals this cycle")

        except Exception as e:
            logger.error(f"Scan loop error: {e}", exc_info=True)

        logger.info(f"⏳ Next scan in {config.SCAN_INTERVAL_MINUTES} minutes...")
        await asyncio.sleep(config.SCAN_INTERVAL_MINUTES * 60)


async def exit_check_loop(bot: TelegramSignalBot, tracker: ExitTracker, fetcher: DataFetcher, marathon: MarathonTracker):
    """Exit tracking loop — checks open positions for SL/TP/time exits."""
    while True:
        try:
            if tracker.position_count > 0:
                logger.debug(f"🔒 Checking {tracker.position_count} open position(s)...")

                def price_getter(symbol: str):
                    return fetcher.get_current_price(symbol)

                alerts = tracker.check_exits(price_getter)

                for alert in alerts:
                    await bot.send_exit_alert(alert)

                    # Record in marathon tracker
                    pos = alert.position
                    trade = marathon.record_trade(
                        symbol=pos.symbol,
                        direction=pos.direction,
                        entry_price=pos.entry_price,
                        exit_price=alert.current_price,
                        pnl_pct=alert.pnl_pct,
                        position_size_pct=pos.position_size_pct,
                        score=pos.score,
                        exit_reason=alert.reason.value,
                    )

                    # Send marathon update
                    marathon_msg = marathon.format_trade_message(trade)
                    await bot.send_status_message(marathon_msg)

                    logger.info(
                        f"📤 Exit alert: {pos.symbol} "
                        f"{alert.reason.value} PnL={alert.pnl_pct:+.2f}% "
                        f"| Marathon: ${marathon.current_balance:.2f}"
                    )

        except Exception as e:
            logger.error(f"Exit check error: {e}", exc_info=True)

        await asyncio.sleep(config.EXIT_CHECK_INTERVAL_MINUTES * 60)


async def telegram_polling(bot: TelegramSignalBot):
    """Run Telegram bot command polling."""
    app = bot.build_application()
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    logger.info("📱 Telegram command handler started")


async def main():
    """Main entry point."""
    # ── Init components ─────────────────────────────────
    fetcher = DataFetcher()
    engine = SignalEngine(data_fetcher=fetcher)
    tracker = ExitTracker()
    marathon = MarathonTracker(starting_balance=46.0)

    # ── Init auto-trader (optional) ─────────────────────
    trader: Optional[BybitTrader] = None
    if config.AUTO_TRADE_ENABLED and config.BYBIT_API_KEY:
        try:
            trader = BybitTrader()
            balance = trader.get_balance()
            marathon.current_balance = balance
            marathon._save()
            logger.info(f"💱 Auto-trader ENABLED | Real balance: ${balance:.2f}")
        except Exception as e:
            logger.error(f"❌ Could not init auto-trader: {e}")
            trader = None
    else:
        logger.info("📡 Signal-only mode (AUTO_TRADE=false)")

    bot = TelegramSignalBot(
        signal_engine=engine, exit_tracker=tracker,
        marathon=marathon, trader=trader,
    )

    # ── Print startup banner ────────────────────────────
    trade_mode = "🟢 AUTO-TRADE" if trader else "📡 SIGNALS ONLY"
    logger.info("╔══════════════════════════════════════════╗")
    logger.info("║   🤖 Trading Signal Bot v4 — Starting   ║")
    logger.info("╠══════════════════════════════════════════╣")
    logger.info(f"║  Exchange:  {config.EXCHANGE_ID:<28}║")
    logger.info(f"║  Mode:      {trade_mode:<28}║")
    logger.info(f"║  Pairs:     {len(config.TRADING_PAIRS):<28}║")
    logger.info(f"║  Leverage:  {config.DEFAULT_LEVERAGE}x{' ' * 25}║")
    logger.info(f"║  Threshold: {config.SIGNAL_THRESHOLD}/100{' ' * 23}║")
    logger.info(f"║  Interval:  {config.SCAN_INTERVAL_MINUTES}min{' ' * 24}║")
    logger.info(f"║  BTC filter: {'ON' if config.BTC_FILTER_ENABLED else 'OFF':<27}║")
    logger.info(f"║  Marathon:  ${marathon.current_balance:.2f}{' ' * 20}║")
    logger.info("╚══════════════════════════════════════════╝")

    # ── Send startup notification ───────────────────────
    try:
        await bot.send_status_message(
            "🤖 *Bot v4 Started!*\n\n"
            f"📊 Pairs: {len(config.TRADING_PAIRS)}\n"
            f"💱 Mode: {'🟢 AUTO-TRADE' if trader else '📡 Signals only'}\n"
            f"⚡ Leverage: {config.DEFAULT_LEVERAGE}x\n"
            f"🔍 Scan: every {config.SCAN_INTERVAL_MINUTES}min\n"
            f"🪙 BTC filter: {'ON' if config.BTC_FILTER_ENABLED else 'OFF'}\n"
            f"\n🏁 *Marathon:* `${marathon.current_balance:.2f}` ({marathon.total_pnl_pct:+.1f}%)"
        )
    except Exception as e:
        logger.error(f"Could not send startup message: {e}")

    # ── Start all loops ─────────────────────────────────
    await telegram_polling(bot)
    await asyncio.gather(
        scan_loop(engine, bot, tracker, trader),
        exit_check_loop(bot, tracker, fetcher, marathon),
    )


if __name__ == "__main__":
    # Handle graceful shutdown
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def shutdown_handler(sig, frame):
        logger.info(f"Received signal {sig}, shutting down...")
        for task in asyncio.all_tasks(loop):
            task.cancel()

    os_signal.signal(os_signal.SIGINT, shutdown_handler)
    os_signal.signal(os_signal.SIGTERM, shutdown_handler)

    try:
        loop.run_until_complete(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("🛑 Shutting down…")
    finally:
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()
        logger.info("👋 Bye!")
