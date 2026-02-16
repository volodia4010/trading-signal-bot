"""
Marathon Tracker — tracks a challenge from starting balance, recording every trade PnL.
Saves state to JSON so it survives restarts.
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import config

logger = logging.getLogger(__name__)

MARATHON_FILE = Path(__file__).parent / "marathon_data.json"


@dataclass
class Trade:
    """A single trade record."""
    symbol: str
    direction: str
    entry_price: float
    exit_price: float
    pnl_pct: float
    pnl_usd: float
    position_size_pct: float
    score: int
    balance_before: float
    balance_after: float
    exit_reason: str
    timestamp: str


class MarathonTracker:
    """
    Tracks a balance-growing marathon challenge.
    Records every closed trade and persists to disk.
    """

    def __init__(self, starting_balance: float = 46.0):
        self.starting_balance = starting_balance
        self.current_balance = starting_balance
        self.trades: list[Trade] = []
        self.started_at: str = datetime.now(timezone.utc).isoformat()
        self._load()

    # ── Persistence ─────────────────────────────────────

    def _save(self):
        """Save marathon state to JSON."""
        data = {
            "starting_balance": self.starting_balance,
            "current_balance": self.current_balance,
            "started_at": self.started_at,
            "trades": [asdict(t) for t in self.trades],
        }
        try:
            MARATHON_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        except Exception as e:
            logger.error(f"Failed to save marathon data: {e}")

    def _load(self):
        """Load marathon state from JSON if exists."""
        if not MARATHON_FILE.exists():
            logger.info(f"🏁 New marathon started! Balance: ${self.starting_balance}")
            self._save()
            return

        try:
            data = json.loads(MARATHON_FILE.read_text())
            self.starting_balance = data["starting_balance"]
            self.current_balance = data["current_balance"]
            self.started_at = data["started_at"]
            self.trades = [Trade(**t) for t in data.get("trades", [])]
            logger.info(
                f"📂 Marathon loaded: ${self.current_balance:.2f} "
                f"({len(self.trades)} trades)"
            )
        except Exception as e:
            logger.error(f"Failed to load marathon data: {e}")

    # ── Trade Recording ─────────────────────────────────

    def record_trade(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        exit_price: float,
        pnl_pct: float,
        position_size_pct: float,
        score: int,
        exit_reason: str,
    ) -> Trade:
        """
        Record a closed trade and update the balance.
        PnL is applied to the allocated portion of balance.
        """
        balance_before = self.current_balance

        # Calculate $ PnL: position_size_pct of balance * pnl_pct
        position_usd = self.current_balance * (position_size_pct / 100)
        pnl_usd = position_usd * (pnl_pct / 100)

        self.current_balance += pnl_usd

        trade = Trade(
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            exit_price=exit_price,
            pnl_pct=round(pnl_pct, 2),
            pnl_usd=round(pnl_usd, 2),
            position_size_pct=position_size_pct,
            score=score,
            balance_before=round(balance_before, 2),
            balance_after=round(self.current_balance, 2),
            exit_reason=exit_reason,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        self.trades.append(trade)
        self._save()

        logger.info(
            f"💰 Marathon trade: {symbol} {direction} | "
            f"PnL: ${pnl_usd:+.2f} ({pnl_pct:+.2f}%) | "
            f"Balance: ${balance_before:.2f} → ${self.current_balance:.2f}"
        )

        return trade

    # ── Stats ───────────────────────────────────────────

    @property
    def total_pnl_usd(self) -> float:
        return self.current_balance - self.starting_balance

    @property
    def total_pnl_pct(self) -> float:
        if self.starting_balance == 0:
            return 0
        return (self.current_balance - self.starting_balance) / self.starting_balance * 100

    @property
    def win_count(self) -> int:
        return sum(1 for t in self.trades if t.pnl_usd > 0)

    @property
    def loss_count(self) -> int:
        return sum(1 for t in self.trades if t.pnl_usd <= 0)

    @property
    def winrate(self) -> float:
        total = len(self.trades)
        return (self.win_count / total * 100) if total > 0 else 0

    @property
    def best_trade(self) -> Optional[Trade]:
        return max(self.trades, key=lambda t: t.pnl_usd) if self.trades else None

    @property
    def worst_trade(self) -> Optional[Trade]:
        return min(self.trades, key=lambda t: t.pnl_usd) if self.trades else None

    @property
    def max_balance(self) -> float:
        if not self.trades:
            return self.starting_balance
        return max(t.balance_after for t in self.trades)

    @property
    def drawdown_pct(self) -> float:
        if self.max_balance == 0:
            return 0
        return (self.current_balance - self.max_balance) / self.max_balance * 100

    # ── Progress Bar ────────────────────────────────────

    def _progress_bar(self, current: float, target: float, length: int = 15) -> str:
        """Visual progress bar."""
        if target <= 0:
            return "░" * length
        pct = min(current / target, 1.0)
        filled = int(length * pct)
        return "█" * filled + "░" * (length - filled)

    # ── Telegram Messages ───────────────────────────────

    def format_trade_message(self, trade: Trade) -> str:
        """Format a trade as a marathon update message."""
        pnl_emoji = "✅" if trade.pnl_usd > 0 else "❌"
        growth = self.total_pnl_pct

        # Milestones
        milestones = [100, 250, 500, 1000, 2500, 5000, 10000]
        next_target = next((m for m in milestones if m > self.current_balance), milestones[-1])
        progress = self._progress_bar(self.current_balance, next_target)

        msg = (
            f"{'━' * 30}\n"
            f"🏃 *МАРАФОН — Оновлення*\n"
            f"{'━' * 30}\n"
            f"\n"
            f"{pnl_emoji} *{trade.symbol}* {trade.direction}\n"
            f"   PnL: `{trade.pnl_pct:+.2f}%` (`${trade.pnl_usd:+.2f}`)\n"
            f"   Причина: {trade.exit_reason}\n"
            f"\n"
            f"💰 *Баланс:* `${trade.balance_before:.2f}` → `${trade.balance_after:.2f}`\n"
            f"📊 *Загальний ріст:* `{growth:+.1f}%`\n"
            f"\n"
            f"🎯 Ціль ${next_target}: {progress} {self.current_balance / next_target * 100:.0f}%\n"
            f"{'━' * 30}"
        )
        return msg

    def format_status(self) -> str:
        """Full marathon status for /marathon command."""
        days = 0
        try:
            started = datetime.fromisoformat(self.started_at)
            days = (datetime.now(timezone.utc) - started).days
        except Exception:
            pass

        growth = self.total_pnl_pct
        growth_emoji = "📈" if growth >= 0 else "📉"

        # Milestones check
        milestones = [100, 250, 500, 1000, 2500, 5000, 10000]
        next_target = next((m for m in milestones if m > self.current_balance), milestones[-1])
        progress = self._progress_bar(self.current_balance, next_target)

        # Milestone achievements
        achieved = [m for m in milestones if self.max_balance >= m]
        achieved_text = ", ".join(f"${m}" for m in achieved) if achieved else "—"

        msg = (
            f"{'━' * 30}\n"
            f"🏁 *МАРАФОН $46 → $???*\n"
            f"{'━' * 30}\n"
            f"\n"
            f"💰 *Поточний баланс:* `${self.current_balance:.2f}`\n"
            f"🏦 *Старт:* `${self.starting_balance:.2f}`\n"
            f"{growth_emoji} *Ріст:* `{growth:+.1f}%` (`${self.total_pnl_usd:+.2f}`)\n"
            f"📆 *Днів:* {days}\n"
            f"\n"
            f"📊 *Статистика:*\n"
            f"  Угод: {len(self.trades)}\n"
            f"  ✅ Wins: {self.win_count} | ❌ Losses: {self.loss_count}\n"
            f"  🎯 Winrate: {self.winrate:.0f}%\n"
            f"  📈 Макс баланс: `${self.max_balance:.2f}`\n"
            f"  📉 Drawdown: `{self.drawdown_pct:.1f}%`\n"
        )

        if self.best_trade:
            msg += f"  🏆 Кращий: {self.best_trade.symbol} `${self.best_trade.pnl_usd:+.2f}`\n"
        if self.worst_trade:
            msg += f"  💀 Гірший: {self.worst_trade.symbol} `${self.worst_trade.pnl_usd:+.2f}`\n"

        msg += (
            f"\n"
            f"🎯 *Наступна ціль:* ${next_target}\n"
            f"  {progress} {self.current_balance / next_target * 100:.0f}%\n"
            f"\n"
            f"🏅 *Досягнення:* {achieved_text}\n"
            f"{'━' * 30}"
        )

        # Last 5 trades
        if self.trades:
            msg += f"\n\n📋 *Останні угоди:*\n"
            for t in self.trades[-5:]:
                emoji = "✅" if t.pnl_usd > 0 else "❌"
                msg += f"  {emoji} {t.symbol} {t.direction} `${t.pnl_usd:+.2f}` → `${t.balance_after:.2f}`\n"

        return msg

    def reset(self, new_balance: float = 46.0):
        """Reset the marathon."""
        self.starting_balance = new_balance
        self.current_balance = new_balance
        self.trades = []
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._save()
        logger.info(f"🏁 Marathon RESET! New balance: ${new_balance}")
