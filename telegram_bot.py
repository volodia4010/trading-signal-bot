"""
Telegram bot — formats signals and sends them to the configured chat.
v3: Added exit alerts, position tracking, BTC filter info, position sizing.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from telegram import Update, Bot
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

import config
from signal_engine import Signal, SignalEngine
from indicators import Direction
from exit_tracker import ExitTracker, ExitAlert
from marathon import MarathonTracker
from trader import BybitTrader

logger = logging.getLogger(__name__)


class TelegramSignalBot:
    """Sends trading signals via Telegram and handles basic commands."""

    def __init__(
        self,
        signal_engine: Optional[SignalEngine] = None,
        exit_tracker: Optional[ExitTracker] = None,
        marathon: Optional[MarathonTracker] = None,
        trader: Optional[BybitTrader] = None,
    ):
        self.bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
        self.engine = signal_engine
        self.tracker = exit_tracker or ExitTracker()
        self.marathon = marathon
        self.trader = trader
        self._last_sent: dict[str, datetime] = {}
        self._app: Optional[Application] = None

    def format_signal(self, signal: Signal) -> str:
        """Format a Signal into a Telegram message with markdown."""
        direction_emoji = "🟢 LONG" if signal.direction == Direction.LONG else "🔴 SHORT"

        # Indicator list
        indicators_text = ""
        for ind in signal.primary_indicators:
            indicators_text += f"  • {ind.name}: {ind.description}\n"

        # Extra indicators (Funding, OI, S/R)
        extra_text = ""
        for ind in signal.extra_indicators:
            if ind.direction != Direction.NEUTRAL or ind.confidence > 0:
                emoji = "🟢" if ind.direction == Direction.LONG else "🔴" if ind.direction == Direction.SHORT else "⚪"
                extra_text += f"  {emoji} {ind.description}\n"
            else:
                extra_text += f"  ⚪ {ind.description}\n"

        # S/R levels
        sr_text = ""
        if signal.sr_levels:
            supports = signal.sr_levels.get("support", [])
            resistances = signal.sr_levels.get("resistance", [])
            if supports:
                nearest_sup = max(supports, key=lambda x: x[0])
                sr_text += f"  🟢 Support: `{nearest_sup[0]:,.2f}` ({nearest_sup[1]}x)\n"
            if resistances:
                nearest_res = min(resistances, key=lambda x: x[0])
                sr_text += f"  🔴 Resistance: `{nearest_res[0]:,.2f}` ({nearest_res[1]}x)\n"

        # Confirmation
        confirm_emoji = "✅" if signal.confirmation_tf_aligned else "⚠️"

        # Price formatting
        price = signal.current_price
        if price > 100:
            fmt = ",.2f"
        elif price > 1:
            fmt = ",.4f"
        else:
            fmt = ",.6f"

        msg = (
            f"{'━' * 30}\n"
            f"📊 *{signal.symbol}* — {direction_emoji}\n"
            f"{'━' * 30}\n"
            f"\n"
            f"💯 *Score:* {signal.score}/100 {signal.strength}\n"
            f"💰 *Price:* `{signal.current_price:{fmt}}`\n"
            f"💼 *Розмір позиції:* {signal.position_size_pct}% депозиту\n"
            f"\n"
            f"📍 *Entry Zone:*\n"
            f"   `{signal.entry_zone[0]:{fmt}}` — `{signal.entry_zone[1]:{fmt}}`\n"
            f"🛑 *Stop Loss:* `{signal.stop_loss:{fmt}}`\n"
            f"🎯 *TP1 ({config.TP_PARTIAL_PCT}%):* `{signal.take_profit_1:{fmt}}`\n"
            f"🎯🎯 *TP2 (100%):* `{signal.take_profit_2:{fmt}}`\n"
            f"📐 *Risk/Reward:* 1:{signal.risk_reward}\n"
            f"⏰ *Авто-вихід:* через {signal.exit_time_hours}h\n"
            f"\n"
            f"📈 *Індикатори:*\n"
            f"{indicators_text}\n"
            f"🔬 *Доп. аналіз:*\n"
            f"{extra_text}"
            f"📊 *Об'єм:* {signal.volume_quality}\n"
        )

        if sr_text:
            msg += f"\n🏗 *Рівні S/R:*\n{sr_text}"

        if signal.btc_filter_info:
            msg += f"\n🪙 *{signal.btc_filter_info}*\n"

        msg += (
            f"\n{confirm_emoji} *{config.CONFIRMATION_TIMEFRAME} Confirmation:*\n"
            f"   {signal.confirmation_details}\n"
            f"\n"
            f"🕐 {signal.timestamp.strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"{'━' * 30}\n"
            f"⚠️ _DYOR — це не фінансова порада!_"
        )
        return msg

    async def send_signal(self, signal: Signal) -> bool:
        """Send a signal to the configured chat, respecting cooldown."""
        if signal.symbol in self._last_sent:
            elapsed = datetime.now(timezone.utc) - self._last_sent[signal.symbol]
            if elapsed < timedelta(minutes=config.SIGNAL_COOLDOWN_MINUTES):
                logger.info(
                    f"Skipping {signal.symbol} — cooldown "
                    f"({elapsed.seconds // 60}m / {config.SIGNAL_COOLDOWN_MINUTES}m)"
                )
                return False

        try:
            message = self.format_signal(signal)
            await self.bot.send_message(
                chat_id=config.TELEGRAM_CHAT_ID,
                text=message,
                parse_mode=ParseMode.MARKDOWN,
            )
            self._last_sent[signal.symbol] = datetime.now(timezone.utc)
            logger.info(f"✅ Sent signal for {signal.symbol}")
            return True
        except Exception as e:
            logger.error(f"Failed to send signal for {signal.symbol}: {e}")
            return False

    async def send_exit_alert(self, alert: ExitAlert) -> bool:
        """Send an exit alert to Telegram."""
        try:
            await self.bot.send_message(
                chat_id=config.TELEGRAM_CHAT_ID,
                text=alert.message,
                parse_mode=ParseMode.MARKDOWN,
            )
            logger.info(f"📤 Sent exit alert for {alert.position.symbol}")
            return True
        except Exception as e:
            logger.error(f"Failed to send exit alert: {e}")
            return False

    async def send_signals(self, signals: list[Signal]) -> int:
        """Send multiple signals. Returns count of sent messages."""
        sent = 0
        for signal in signals:
            if await self.send_signal(signal):
                sent += 1
        return sent

    async def send_status_message(self, text: str):
        """Send a plain status message."""
        try:
            await self.bot.send_message(
                chat_id=config.TELEGRAM_CHAT_ID,
                text=text,
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            logger.error(f"Failed to send status message: {e}")

    # ── Command Handlers ────────────────────────────────

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command."""
        await update.message.reply_text(
            "🤖 *Trading Signal Bot v3*\n\n"
            "Я аналізую ф'ючерсні ринки та надсилаю торгові сигнали.\n\n"
            "*Команди:*\n"
            "/status — статус бота\n"
            "/scan — запустити сканування зараз\n"
            "/pairs — показати список пар\n"
            "/positions — відкриті позиції\n"
            "/history — історія угод\n",
            parse_mode=ParseMode.MARKDOWN,
        )

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command."""
        now = datetime.now(timezone.utc)
        cooldowns = []
        for sym, last in self._last_sent.items():
            elapsed = (now - last).seconds // 60
            cooldowns.append(f"  • {sym}: {elapsed}m ago")

        cooldown_text = "\n".join(cooldowns) if cooldowns else "  Немає нещодавніх сигналів"

        positions_text = self.tracker.get_status_text() if self.tracker else "N/A"

        await update.message.reply_text(
            f"📊 *Статус бота*\n\n"
            f"*Пар:* {len(config.TRADING_PAIRS)}\n"
            f"*Таймфрейм:* {config.PRIMARY_TIMEFRAME} + {config.CONFIRMATION_TIMEFRAME}\n"
            f"*Поріг:* {config.SIGNAL_THRESHOLD}/100\n"
            f"*Інтервал:* кожні {config.SCAN_INTERVAL_MINUTES}хв\n"
            f"*BTC фільтр:* {'✅ Увімкнено' if config.BTC_FILTER_ENABLED else '❌ Вимкнено'}\n"
            f"*Авто-вихід:* {config.EXIT_TIME_HOURS}h\n\n"
            f"*Нещодавні сигнали:*\n{cooldown_text}\n\n"
            f"{positions_text}\n\n"
            f"🕐 {now.strftime('%H:%M UTC')}",
            parse_mode=ParseMode.MARKDOWN,
        )

    async def _cmd_scan(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /scan — trigger manual scan."""
        await update.message.reply_text("🔄 Сканую ринки...")

        if self.engine:
            signals = self.engine.scan_all()
            if signals:
                await update.message.reply_text(
                    f"📡 Знайдено {len(signals)} сигнал(ів)! Надсилаю..."
                )
                await self.send_signals(signals)
            else:
                await update.message.reply_text(
                    "😴 Наразі немає сильних сигналів. "
                    f"(поріг: {config.SIGNAL_THRESHOLD}/100)"
                )
        else:
            await update.message.reply_text("⚠️ Signal engine не ініціалізований")

    async def _cmd_pairs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /pairs command."""
        pairs_list = "\n".join(f"  • {p}" for p in config.TRADING_PAIRS)
        await update.message.reply_text(
            f"📋 *Активні торгові пари:*\n\n{pairs_list}",
            parse_mode=ParseMode.MARKDOWN,
        )

    async def _cmd_positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /positions — show open tracked positions."""
        text = self.tracker.get_status_text() if self.tracker else "N/A"
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /history — show closed position history."""
        text = self.tracker.get_history_summary() if self.tracker else "N/A"
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_marathon(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /marathon — show marathon progress."""
        if self.marathon:
            text = self.marathon.format_status()
        else:
            text = "❌ Marathon not initialized"
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /balance — show real Bybit balance."""
        if self.trader:
            balance = self.trader.get_balance()
            await update.message.reply_text(
                f"💰 *Bybit Баланс:* `${balance:.2f}` USDT",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await update.message.reply_text(
                "📡 Auto-trade вимкнено.\n"
                "Додайте `BYBIT_API_KEY` і `AUTO_TRADE=true` в `.env`",
                parse_mode=ParseMode.MARKDOWN,
            )

    async def _cmd_real(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /real — show real Bybit open positions."""
        if self.trader:
            text = self.trader.format_positions_text()
        else:
            text = "📡 Auto-trade вимкнено"
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    def build_application(self) -> Application:
        """Build and return a Telegram Application with command handlers."""
        self._app = (
            Application.builder()
            .token(config.TELEGRAM_BOT_TOKEN)
            .build()
        )
        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("status", self._cmd_status))
        self._app.add_handler(CommandHandler("scan", self._cmd_scan))
        self._app.add_handler(CommandHandler("pairs", self._cmd_pairs))
        self._app.add_handler(CommandHandler("positions", self._cmd_positions))
        self._app.add_handler(CommandHandler("history", self._cmd_history))
        self._app.add_handler(CommandHandler("marathon", self._cmd_marathon))
        self._app.add_handler(CommandHandler("balance", self._cmd_balance))
        self._app.add_handler(CommandHandler("real", self._cmd_real))
        return self._app
