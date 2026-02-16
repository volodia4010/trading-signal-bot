"""
Bybit Auto-Trader — executes real orders via Bybit API using CCXT.
Opens market orders with SL/TP when a signal is generated.
Tracks real positions and syncs with marathon tracker.
"""

import logging
from typing import Optional
from datetime import datetime, timezone

import ccxt

import config
from signal_engine import Signal
from indicators import Direction

logger = logging.getLogger(__name__)


class BybitTrader:
    """
    Executes real trades on Bybit Futures via CCXT.
    - Opens LONG/SHORT market orders
    - Sets SL and TP orders
    - Fetches real balance and open positions
    - Closes positions
    """

    def __init__(self):
        if not config.BYBIT_API_KEY or not config.BYBIT_API_SECRET:
            raise ValueError(
                "BYBIT_API_KEY and BYBIT_API_SECRET must be set in .env"
            )

        self.exchange = ccxt.bybit({
            "apiKey": config.BYBIT_API_KEY,
            "secret": config.BYBIT_API_SECRET,
            "options": {
                "defaultType": "swap",       # Linear perpetuals
                "adjustForTimeDifference": True,
            },
            "enableRateLimit": True,
        })

        if config.USE_TESTNET:
            self.exchange.set_sandbox_mode(True)
            logger.info("🧪 Bybit TESTNET mode enabled")

        self.exchange.load_markets()
        logger.info("💱 BybitTrader initialized")

    # ── Balance ─────────────────────────────────────────

    def get_balance(self) -> float:
        """Get available USDT balance."""
        try:
            balance = self.exchange.fetch_balance({"type": "swap"})
            usdt = balance.get("USDT", {})
            available = float(usdt.get("free", 0) or 0)
            logger.info(f"💰 Balance: ${available:.2f} USDT")
            return available
        except Exception as e:
            logger.error(f"Error fetching balance: {e}")
            return 0.0

    # ── Leverage ────────────────────────────────────────

    def set_leverage(self, symbol: str, leverage: int = None):
        """Set leverage for a symbol."""
        lev = leverage or config.DEFAULT_LEVERAGE
        try:
            self.exchange.set_leverage(lev, symbol)
            logger.debug(f"Leverage set to {lev}x for {symbol}")
        except Exception as e:
            # Some pairs may already have the leverage set
            logger.debug(f"Leverage note for {symbol}: {e}")

    # ── Open Position ──────────────────────────────────

    def open_position(self, signal: Signal) -> Optional[dict]:
        """
        Open a real position based on a Signal.
        Returns order info dict or None on failure.
        """
        # ── Guard: block duplicate positions ────────────────
        existing = self.get_position_for_symbol(signal.symbol)
        if existing:
            logger.warning(
                f"⚠️ ДУБЛЬ ЗАБЛОКОВАНО: {signal.symbol} вже має позицію "
                f"({existing['side']} {existing['contracts']} contracts)"
            )
            return None

        try:
            symbol = signal.symbol
            side = "buy" if signal.direction == Direction.LONG else "sell"

            # Set leverage
            self.set_leverage(symbol)

            # Calculate position size in USDT
            balance = self.get_balance()
            if balance <= 0:
                logger.error("❌ No available balance")
                return None

            position_usd = balance * (signal.position_size_pct / 100)

            # Get minimum order size and price precision
            market = self.exchange.market(symbol)
            min_cost = market.get("limits", {}).get("cost", {}).get("min", 1)
            if position_usd < (min_cost or 1):
                logger.error(
                    f"❌ Position too small: ${position_usd:.2f} < min ${min_cost}"
                )
                return None

            # Calculate quantity in base asset
            price = signal.current_price
            amount = position_usd * config.DEFAULT_LEVERAGE / price

            # Round to market precision
            amount_precision = market.get("precision", {}).get("amount", 8)
            amount = float(
                self.exchange.amount_to_precision(symbol, amount)
            )

            if amount <= 0:
                logger.error(f"❌ Calculated amount is 0 for {symbol}")
                return None

            logger.info(
                f"📤 Opening {side.upper()} {symbol}: "
                f"${position_usd:.2f} ({signal.position_size_pct}%) | "
                f"Amount: {amount} | Leverage: {config.DEFAULT_LEVERAGE}x"
            )

            # Place market order
            order = self.exchange.create_order(
                symbol=symbol,
                type="market",
                side=side,
                amount=amount,
            )

            order_id = order.get("id", "?")
            fill_price = float(order.get("average", price) or price)

            logger.info(
                f"✅ Order filled: {symbol} {side.upper()} "
                f"#{order_id} @ {fill_price}"
            )

            # Set Stop Loss
            sl_side = "sell" if side == "buy" else "buy"
            try:
                sl_price = float(
                    self.exchange.price_to_precision(symbol, signal.stop_loss)
                )
                self.exchange.create_order(
                    symbol=symbol,
                    type="market",
                    side=sl_side,
                    amount=amount,
                    params={
                        "stopLoss": {
                            "triggerPrice": sl_price,
                            "type": "market",
                        },
                        "reduceOnly": True,
                    },
                )
                logger.info(f"🛑 SL set at {sl_price}")
            except Exception as e:
                logger.warning(f"⚠️ Could not set SL: {e}")
                # Try alternative method - set SL via set_trading_stop
                try:
                    self.exchange.set_trading_stop(
                        symbol,
                        stopLoss=sl_price,
                        params={"positionIdx": 0},
                    )
                    logger.info(f"🛑 SL set via trading stop at {sl_price}")
                except Exception as e2:
                    logger.error(f"❌ Failed to set SL: {e2}")

            # Set Take Profit (TP2 — full target)
            try:
                tp_price = float(
                    self.exchange.price_to_precision(symbol, signal.take_profit_2)
                )
                self.exchange.set_trading_stop(
                    symbol,
                    takeProfit=tp_price,
                    params={"positionIdx": 0},
                )
                logger.info(f"🎯 TP set at {tp_price}")
            except Exception as e:
                logger.warning(f"⚠️ Could not set TP: {e}")

            return {
                "order_id": order_id,
                "symbol": symbol,
                "side": side,
                "amount": amount,
                "fill_price": fill_price,
                "sl": signal.stop_loss,
                "tp1": signal.take_profit_1,
                "tp2": signal.take_profit_2,
                "position_usd": position_usd,
                "leverage": config.DEFAULT_LEVERAGE,
            }

        except ccxt.InsufficientFunds as e:
            logger.error(f"💸 Insufficient funds for {signal.symbol}: {e}")
            return None
        except ccxt.NetworkError as e:
            logger.error(f"🌐 Network error opening {signal.symbol}: {e}")
            return None
        except Exception as e:
            logger.error(f"❌ Error opening position {signal.symbol}: {e}")
            return None

    # ── Close Position ──────────────────────────────────

    def close_position(self, symbol: str) -> Optional[dict]:
        """Close an open position by placing opposite market order."""
        try:
            positions = self.exchange.fetch_positions([symbol])
            pos = None
            for p in positions:
                if p["symbol"] == symbol and float(p.get("contracts", 0) or 0) > 0:
                    pos = p
                    break

            if not pos:
                logger.info(f"No open position for {symbol}")
                return None

            side = "sell" if pos["side"] == "long" else "buy"
            amount = float(pos["contracts"])

            order = self.exchange.create_order(
                symbol=symbol,
                type="market",
                side=side,
                amount=amount,
                params={"reduceOnly": True},
            )

            close_price = float(order.get("average", 0) or 0)
            logger.info(f"🔒 Closed {symbol} @ {close_price}")

            return {
                "symbol": symbol,
                "close_price": close_price,
                "amount": amount,
                "order_id": order.get("id"),
            }

        except Exception as e:
            logger.error(f"Error closing {symbol}: {e}")
            return None

    # ── Position Info ───────────────────────────────────

    def get_open_positions(self) -> list[dict]:
        """Get all open positions."""
        try:
            positions = self.exchange.fetch_positions()
            open_pos = []
            for p in positions:
                contracts = float(p.get("contracts", 0) or 0)
                if contracts > 0:
                    open_pos.append({
                        "symbol": p["symbol"],
                        "side": p["side"],
                        "contracts": contracts,
                        "entry_price": float(p.get("entryPrice", 0) or 0),
                        "mark_price": float(p.get("markPrice", 0) or 0),
                        "unrealized_pnl": float(p.get("unrealizedPnl", 0) or 0),
                        "leverage": float(p.get("leverage", 0) or 0),
                        "notional": float(p.get("notional", 0) or 0),
                    })
            return open_pos
        except Exception as e:
            logger.error(f"Error fetching positions: {e}")
            return []

    def get_position_for_symbol(self, symbol: str) -> Optional[dict]:
        """Get position info for a specific symbol."""
        positions = self.get_open_positions()
        for p in positions:
            if p["symbol"] == symbol:
                return p
        return None

    def get_real_pnl(self, symbol: str) -> Optional[float]:
        """Get unrealized PnL for a symbol."""
        pos = self.get_position_for_symbol(symbol)
        if pos:
            return pos["unrealized_pnl"]
        return None

    # ── Status ──────────────────────────────────────────

    def format_positions_text(self) -> str:
        """Format open positions for Telegram."""
        positions = self.get_open_positions()
        if not positions:
            return "📭 Немає відкритих позицій на Bybit"

        balance = self.get_balance()
        lines = [
            f"💱 *Bybit Positions* (Balance: `${balance:.2f}`):\n"
        ]
        for p in positions:
            pnl = p["unrealized_pnl"]
            pnl_emoji = "✅" if pnl >= 0 else "❌"
            side_emoji = "🟢" if p["side"] == "long" else "🔴"
            lines.append(
                f"  {side_emoji} *{p['symbol']}* {p['side'].upper()}\n"
                f"    Entry: `{p['entry_price']:,.2f}` | Mark: `{p['mark_price']:,.2f}`\n"
                f"    {pnl_emoji} PnL: `${pnl:+.2f}` | {p['leverage']:.0f}x"
            )
        return "\n".join(lines)
