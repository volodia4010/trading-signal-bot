"""
Exit Tracker — monitors open signals and sends alerts for SL/TP hits and time-based exits.
Tracks each signal from entry, notifies when:
  - Price hits Stop Loss → EXIT alert
  - Price hits Take Profit 1 (partial) → PARTIAL TP alert
  - Price hits Take Profit 2 (full) → FULL TP alert
  - Time expires → TIME EXIT alert
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional
from enum import Enum

import config

logger = logging.getLogger(__name__)


class ExitReason(Enum):
    STOP_LOSS = "🛑 STOP LOSS"
    TAKE_PROFIT_1 = "🎯 TAKE PROFIT 1 (Partial)"
    TAKE_PROFIT_2 = "🎯🎯 TAKE PROFIT 2 (Full)"
    TIME_EXIT = "⏰ ВИХІД ПО ЧАСУ"
    MANUAL = "✋ Manual"


class PositionStatus(Enum):
    OPEN = "open"
    TP1_HIT = "tp1_hit"       # Partial TP taken, trailing rest
    CLOSED = "closed"


@dataclass
class TrackedPosition:
    """A signal being tracked for exit conditions."""
    symbol: str
    direction: str                    # "LONG" or "SHORT"
    entry_price: float
    stop_loss: float
    take_profit_1: float              # Partial TP (ATR * 1.5)
    take_profit_2: float              # Full TP (ATR * 3.0)
    score: int
    position_size_pct: float          # % of deposit
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: PositionStatus = PositionStatus.OPEN
    exit_reason: Optional[ExitReason] = None
    exit_price: Optional[float] = None
    closed_at: Optional[datetime] = None

    @property
    def age_hours(self) -> float:
        """How many hours since entry."""
        delta = datetime.now(timezone.utc) - self.opened_at
        return delta.total_seconds() / 3600

    @property
    def is_expired(self) -> bool:
        """Whether the position exceeded the time limit."""
        return self.age_hours >= config.EXIT_TIME_HOURS

    @property
    def pnl_pct(self) -> Optional[float]:
        """Unrealized PnL in % (needs current price)."""
        return None  # Calculated externally with current price

    def calc_pnl_pct(self, current_price: float) -> float:
        """Calculate PnL % given current price."""
        if self.direction == "LONG":
            return (current_price - self.entry_price) / self.entry_price * 100
        else:
            return (self.entry_price - current_price) / self.entry_price * 100


@dataclass
class ExitAlert:
    """An exit event to be sent as Telegram notification."""
    position: TrackedPosition
    reason: ExitReason
    current_price: float
    pnl_pct: float
    message: str


class ExitTracker:
    """Manages open positions and checks for exit conditions."""

    def __init__(self):
        self._positions: dict[str, TrackedPosition] = {}  # symbol → position
        self._history: list[TrackedPosition] = []          # closed positions

    @property
    def open_positions(self) -> list[TrackedPosition]:
        return [p for p in self._positions.values() if p.status != PositionStatus.CLOSED]

    @property
    def position_count(self) -> int:
        return len(self.open_positions)

    def add_position(self, position: TrackedPosition):
        """Start tracking a new position."""
        # Close existing position for same symbol if any
        if position.symbol in self._positions:
            old = self._positions[position.symbol]
            if old.status != PositionStatus.CLOSED:
                logger.info(f"Replacing existing position for {position.symbol}")
                old.status = PositionStatus.CLOSED
                old.exit_reason = ExitReason.MANUAL
                old.closed_at = datetime.now(timezone.utc)
                self._history.append(old)

        self._positions[position.symbol] = position
        logger.info(
            f"📌 Tracking {position.symbol} {position.direction} "
            f"@ {position.entry_price:.2f} | SL={position.stop_loss:.2f} | "
            f"TP1={position.take_profit_1:.2f} | TP2={position.take_profit_2:.2f} | "
            f"Size={position.position_size_pct}% | "
            f"Exit in {config.EXIT_TIME_HOURS}h"
        )

    def check_exits(self, price_getter) -> list[ExitAlert]:
        """
        Check all open positions for exit conditions.
        price_getter: callable(symbol) → float or None
        Returns list of ExitAlerts for positions that should be closed.
        """
        alerts = []

        for symbol, pos in list(self._positions.items()):
            if pos.status == PositionStatus.CLOSED:
                continue

            current_price = price_getter(symbol)
            if current_price is None:
                continue

            pnl = pos.calc_pnl_pct(current_price)

            # ── Check Stop Loss ──────────────────────────
            if pos.direction == "LONG" and current_price <= pos.stop_loss:
                alert = self._close_position(pos, ExitReason.STOP_LOSS, current_price, pnl)
                alerts.append(alert)
                continue

            if pos.direction == "SHORT" and current_price >= pos.stop_loss:
                alert = self._close_position(pos, ExitReason.STOP_LOSS, current_price, pnl)
                alerts.append(alert)
                continue

            # ── Check Take Profit 1 (partial) ────────────
            if pos.status == PositionStatus.OPEN:
                if pos.direction == "LONG" and current_price >= pos.take_profit_1:
                    alert = self._partial_tp(pos, current_price, pnl)
                    alerts.append(alert)
                    continue

                if pos.direction == "SHORT" and current_price <= pos.take_profit_1:
                    alert = self._partial_tp(pos, current_price, pnl)
                    alerts.append(alert)
                    continue

            # ── Check Take Profit 2 (full) ───────────────
            if pos.direction == "LONG" and current_price >= pos.take_profit_2:
                alert = self._close_position(pos, ExitReason.TAKE_PROFIT_2, current_price, pnl)
                alerts.append(alert)
                continue

            if pos.direction == "SHORT" and current_price <= pos.take_profit_2:
                alert = self._close_position(pos, ExitReason.TAKE_PROFIT_2, current_price, pnl)
                alerts.append(alert)
                continue

            # ── Check Time Exit ──────────────────────────
            if pos.is_expired:
                alert = self._close_position(pos, ExitReason.TIME_EXIT, current_price, pnl)
                alerts.append(alert)
                continue

        return alerts

    def _close_position(
        self, pos: TrackedPosition, reason: ExitReason,
        current_price: float, pnl: float
    ) -> ExitAlert:
        """Close a position and create an alert."""
        pos.status = PositionStatus.CLOSED
        pos.exit_reason = reason
        pos.exit_price = current_price
        pos.closed_at = datetime.now(timezone.utc)
        self._history.append(pos)

        pnl_emoji = "✅" if pnl >= 0 else "❌"
        msg = (
            f"{'━' * 30}\n"
            f"{reason.value}\n"
            f"{'━' * 30}\n"
            f"\n"
            f"📊 *{pos.symbol}* — {pos.direction}\n"
            f"📍 Entry: `{pos.entry_price:,.2f}`\n"
            f"📍 Exit:  `{current_price:,.2f}`\n"
            f"\n"
            f"{pnl_emoji} *PnL:* `{pnl:+.2f}%`\n"
            f"💰 Розмір: {pos.position_size_pct}% депозиту\n"
            f"⏱ Тривалість: {pos.age_hours:.1f}h\n"
            f"\n"
            f"🕐 {pos.closed_at.strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"{'━' * 30}"
        )

        logger.info(f"{reason.value}: {pos.symbol} PnL={pnl:+.2f}%")

        return ExitAlert(
            position=pos,
            reason=reason,
            current_price=current_price,
            pnl_pct=pnl,
            message=msg,
        )

    def _partial_tp(
        self, pos: TrackedPosition, current_price: float, pnl: float
    ) -> ExitAlert:
        """Partial take profit — move SL to breakeven and alert."""
        pos.status = PositionStatus.TP1_HIT

        # Move stop loss to breakeven (entry price)
        old_sl = pos.stop_loss
        pos.stop_loss = pos.entry_price

        msg = (
            f"{'━' * 30}\n"
            f"{ExitReason.TAKE_PROFIT_1.value}\n"
            f"{'━' * 30}\n"
            f"\n"
            f"📊 *{pos.symbol}* — {pos.direction}\n"
            f"📍 Entry: `{pos.entry_price:,.2f}`\n"
            f"📍 TP1:   `{current_price:,.2f}`\n"
            f"\n"
            f"✅ *PnL:* `{pnl:+.2f}%`\n"
            f"💡 Закрийте {config.TP_PARTIAL_PCT}% позиції!\n"
            f"🔒 SL переміщено → беззбитковість (`{pos.entry_price:,.2f}`)\n"
            f"🎯 Чекаємо TP2: `{pos.take_profit_2:,.2f}`\n"
            f"\n"
            f"🕐 {datetime.now(timezone.utc).strftime('%H:%M UTC')}\n"
            f"{'━' * 30}"
        )

        logger.info(
            f"TP1 HIT: {pos.symbol} PnL={pnl:+.2f}% | "
            f"SL moved {old_sl:.2f} → {pos.entry_price:.2f} (breakeven)"
        )

        return ExitAlert(
            position=pos,
            reason=ExitReason.TAKE_PROFIT_1,
            current_price=current_price,
            pnl_pct=pnl,
            message=msg,
        )

    def get_status_text(self) -> str:
        """Get a summary of all tracked positions for /status command."""
        if not self.open_positions:
            return "📭 Немає відкритих позицій"

        lines = [f"📋 *Відкриті позиції ({self.position_count}):*\n"]
        for pos in self.open_positions:
            status = "🟡 TP1 hit" if pos.status == PositionStatus.TP1_HIT else "🟢 Open"
            lines.append(
                f"  • *{pos.symbol}* {pos.direction} | {status}\n"
                f"    Entry: `{pos.entry_price:,.2f}` | SL: `{pos.stop_loss:,.2f}`\n"
                f"    TP1: `{pos.take_profit_1:,.2f}` | TP2: `{pos.take_profit_2:,.2f}`\n"
                f"    Age: {pos.age_hours:.1f}h / {config.EXIT_TIME_HOURS}h | Size: {pos.position_size_pct}%"
            )
        return "\n".join(lines)

    def get_history_summary(self) -> str:
        """Get summary of recently closed positions."""
        if not self._history:
            return "📭 Немає закритих позицій"

        recent = self._history[-10:]  # Last 10
        wins = sum(1 for p in self._history if p.exit_reason in
                   (ExitReason.TAKE_PROFIT_1, ExitReason.TAKE_PROFIT_2))
        losses = sum(1 for p in self._history if p.exit_reason == ExitReason.STOP_LOSS)
        total = len(self._history)
        winrate = (wins / total * 100) if total > 0 else 0

        lines = [
            f"📊 *Історія ({total} позицій):*\n"
            f"  ✅ Wins: {wins} | ❌ Losses: {losses} | Winrate: {winrate:.0f}%\n"
        ]

        for p in reversed(recent):
            pnl = p.calc_pnl_pct(p.exit_price) if p.exit_price else 0
            emoji = "✅" if pnl >= 0 else "❌"
            lines.append(
                f"  {emoji} {p.symbol} {p.direction} | "
                f"{pnl:+.2f}% | {p.exit_reason.value if p.exit_reason else '?'}"
            )

        return "\n".join(lines)
