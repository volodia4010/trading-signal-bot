"""
Central configuration for the Trading Signal Bot.
All settings are defined here for easy tuning.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Exchange ────────────────────────────────────────────────
EXCHANGE_ID = "bybit"

# ── Bybit Trading API ─────────────────────────────────────
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", "")
AUTO_TRADE_ENABLED = os.getenv("AUTO_TRADE", "false").lower() == "true"
DEFAULT_LEVERAGE = 10              # Leverage for futures positions
MAX_OPEN_POSITIONS = 3              # Max concurrent positions
USE_TESTNET = os.getenv("BYBIT_TESTNET", "false").lower() == "true"

# ── Trading Pairs (Bybit Linear Perpetuals) ─────────────
TRADING_PAIRS = [
    # Top-10 by volume
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "BNB/USDT:USDT",
    "XRP/USDT:USDT",
    "DOGE/USDT:USDT",
    "ADA/USDT:USDT",
    "AVAX/USDT:USDT",
    "LINK/USDT:USDT",
    "DOT/USDT:USDT",
    # Mid-caps with good volatility
    "POL/USDT:USDT",
    "SUI/USDT:USDT",
    "APT/USDT:USDT",
    "OP/USDT:USDT",
    "ARB/USDT:USDT",
    "FIL/USDT:USDT",
    "NEAR/USDT:USDT",
    "ATOM/USDT:USDT",
    "LTC/USDT:USDT",
    "UNI/USDT:USDT",
    # High-volatility plays
    "WIF/USDT:USDT",
    "1000PEPE/USDT:USDT",
    "AAVE/USDT:USDT",
    "INJ/USDT:USDT",
    "TIA/USDT:USDT",
]

# ── Timeframes ──────────────────────────────────────────
PRIMARY_TIMEFRAME = "1h"          # Main analysis timeframe
CONFIRMATION_TIMEFRAME = "4h"     # Higher TF for trend confirmation
CANDLE_LIMIT = 200                # Number of candles to fetch

# ── Signal Scoring ──────────────────────────────────────
SIGNAL_THRESHOLD = 70             # Minimum score (0-100) to send signal
CONFIRMATION_MULTIPLIER = 1.3     # Bonus multiplier when 4h trend confirms

# ── Indicator Parameters ───────────────────────────────
EMA_FAST = 9
EMA_SLOW = 21
RSI_PERIOD = 14
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
BB_PERIOD = 20
BB_STD = 2.0
STOCH_RSI_PERIOD = 14
STOCH_RSI_K = 3
STOCH_RSI_D = 3
STOCH_RSI_OVERSOLD = 20
STOCH_RSI_OVERBOUGHT = 80
ADX_PERIOD = 14
ADX_THRESHOLD = 25
VOLUME_MULTIPLIER = 1.5           # Volume must be 1.5x average
ATR_PERIOD = 14
ATR_SL_MULTIPLIER = 1.5           # Stop-loss = ATR * 1.5
ATR_TP_MULTIPLIER = 3.0           # Take-profit = ATR * 3.0 (2:1 R:R)

# ── Scheduler ──────────────────────────────────────────
SCAN_INTERVAL_MINUTES = 5         # How often to scan markets
SIGNAL_COOLDOWN_MINUTES = 60      # Min time between signals for same pair

# ── BTC Market Filter ─────────────────────────────────
BTC_FILTER_ENABLED = True
BTC_FILTER_SYMBOL = "BTC/USDT:USDT"
BTC_DROP_THRESHOLD_PCT = -1.0     # If BTC drops > 1% in 1h → block longs
BTC_PUMP_THRESHOLD_PCT = 1.0     # If BTC pumps > 1% in 1h → block shorts

# ── Position Sizing ────────────────────────────────────
# Score-based position sizing (% of total deposit)
POSITION_SIZE_MODERATE = 5.0      # Score 70–89 → 5% of deposit
POSITION_SIZE_STRONG = 10.0       # Score 90+   → 10% of deposit

# ── Exit Management ───────────────────────────────────
EXIT_TIME_HOURS = 4               # Auto-exit alert after N hours if no SL/TP hit
EXIT_CHECK_INTERVAL_MINUTES = 5   # How often to check open positions
TP_PARTIAL_PCT = 50               # Close 50% at TP1 (half of full TP)
TP1_MULTIPLIER = 1.5              # TP1 = ATR * 1.5 (partial take profit)
TP2_MULTIPLIER = 3.0              # TP2 = ATR * 3.0 (full take profit)

# ── Volume Filter ─────────────────────────────────────
VOLUME_SPIKE_MULTIPLIER = 2.5     # Strong volume confirmation threshold
VOLUME_MIN_RATIO = 1.0            # Minimum: current vol >= avg (filter dust)

# ── Logging ────────────────────────────────────────────
LOG_LEVEL = "INFO"

