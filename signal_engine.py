"""
Signal Engine — core logic for multi-timeframe analysis and signal scoring.
v3: Added BTC market filter, position sizing, dual TP levels, enhanced volume.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timezone

import config
from data_fetcher import DataFetcher
from indicators import (
    Direction, IndicatorResult,
    ALL_INDICATORS, TREND_INDICATORS,
    calculate_atr,
    calculate_funding_rate,
    calculate_open_interest,
    analyze_support_resistance,
)

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    """A complete trading signal ready for sending."""
    symbol: str
    direction: Direction
    score: int                          # 0–100
    strength: str                       # "Strong" / "Very Strong"
    current_price: float
    entry_zone: tuple[float, float]     # (low, high)
    stop_loss: float
    take_profit_1: float                # Partial TP (closer)
    take_profit_2: float                # Full TP (farther)
    risk_reward: float
    position_size_pct: float            # % of deposit to risk
    primary_indicators: list[IndicatorResult]
    extra_indicators: list[IndicatorResult]
    confirmation_tf_aligned: bool
    confirmation_details: str
    sr_levels: Optional[dict] = None
    btc_filter_info: str = ""
    volume_quality: str = ""            # Volume assessment
    exit_time_hours: int = config.EXIT_TIME_HOURS
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class SignalEngine:
    """Analyzes markets and produces scored trading signals."""

    def __init__(self, data_fetcher: Optional[DataFetcher] = None):
        self.fetcher = data_fetcher or DataFetcher()
        self._btc_change_1h: Optional[float] = None  # cached BTC 1h change

    def _check_btc_filter(self) -> tuple[bool, str]:
        """
        Check BTC market condition.
        Returns (longs_allowed, shorts_allowed, info_string).
        """
        if not config.BTC_FILTER_ENABLED:
            return True, "BTC filter disabled"

        df_btc = self.fetcher.fetch_ohlcv(config.BTC_FILTER_SYMBOL, "1h")
        if df_btc is None or len(df_btc) < 2:
            return True, "⚠️ BTC data unavailable"

        # Calculate BTC change over last 1h candle
        price_now = float(df_btc["close"].iloc[-1])
        price_1h_ago = float(df_btc["close"].iloc[-2])
        btc_change = (price_now - price_1h_ago) / price_1h_ago * 100
        self._btc_change_1h = btc_change

        info = f"BTC 1h: {btc_change:+.2f}%"

        if btc_change <= config.BTC_DROP_THRESHOLD_PCT:
            info += f" ⛔ LONGS BLOCKED (BTC -{abs(btc_change):.2f}%)"
        elif btc_change >= config.BTC_PUMP_THRESHOLD_PCT:
            info += f" ⛔ SHORTS BLOCKED (BTC +{btc_change:.2f}%)"
        else:
            info += " ✅ Normal"

        return True, info

    def _get_position_size(self, score: int) -> float:
        """Determine position size (% of deposit) based on score."""
        if score >= 90:
            return config.POSITION_SIZE_STRONG
        else:
            return config.POSITION_SIZE_MODERATE

    def _check_volume_quality(self, df) -> tuple[str, float]:
        """
        Enhanced volume verification.
        Returns (quality_label, volume_bonus).
        """
        if df is None or len(df) < 20:
            return "⚠️ Insufficient data", 0

        current_vol = float(df["volume"].iloc[-1])
        avg_vol = float(df["volume"].iloc[-20:].mean())

        if avg_vol == 0:
            return "⚠️ Zero volume", 0

        ratio = current_vol / avg_vol

        if ratio < config.VOLUME_MIN_RATIO:
            return f"❌ Низький об'єм ({ratio:.1f}x avg) — DUST", -10
        elif ratio >= config.VOLUME_SPIKE_MULTIPLIER:
            return f"🔥 Spike ({ratio:.1f}x avg)", 8
        elif ratio >= config.VOLUME_MULTIPLIER:
            return f"✅ Вище середнього ({ratio:.1f}x avg)", 5
        elif ratio >= config.VOLUME_MIN_RATIO:
            return f"⚪ Нормальний ({ratio:.1f}x avg)", 0
        else:
            return f"⚠️ Слабкий ({ratio:.1f}x avg)", -5

    def analyze_pair(self, symbol: str, btc_info: str = "") -> Optional[Signal]:
        """
        Analyze a single pair across primary and confirmation timeframes.
        Returns a Signal if score >= threshold, else None.
        """
        # ── Fetch data ──────────────────────────────────────
        df_primary = self.fetcher.fetch_ohlcv(symbol, config.PRIMARY_TIMEFRAME)
        df_confirm = self.fetcher.fetch_ohlcv(symbol, config.CONFIRMATION_TIMEFRAME)

        if df_primary is None or len(df_primary) < 50:
            logger.warning(f"{symbol}: insufficient primary data")
            return None

        # ── Run all standard indicators on primary TF ──────
        primary_results: list[IndicatorResult] = []
        for indicator_fn in ALL_INDICATORS:
            result = indicator_fn(df_primary)
            primary_results.append(result)

        # ── Count votes ────────────────────────────────────
        long_votes = [r for r in primary_results if r.direction == Direction.LONG]
        short_votes = [r for r in primary_results if r.direction == Direction.SHORT]
        neutral_votes = [r for r in primary_results if r.direction == Direction.NEUTRAL]

        # Determine dominant direction
        if len(long_votes) > len(short_votes) and len(long_votes) >= 2:
            direction = Direction.LONG
            active_votes = long_votes
        elif len(short_votes) > len(long_votes) and len(short_votes) >= 2:
            direction = Direction.SHORT
            active_votes = short_votes
        else:
            logger.debug(f"{symbol}: no consensus (L:{len(long_votes)} S:{len(short_votes)} N:{len(neutral_votes)})")
            return None

        # ── BTC Market Filter ─────────────────────────────
        if config.BTC_FILTER_ENABLED and self._btc_change_1h is not None:
            if direction == Direction.LONG and self._btc_change_1h <= config.BTC_DROP_THRESHOLD_PCT:
                logger.info(f"⛔ {symbol} LONG blocked — BTC dropped {self._btc_change_1h:.2f}%")
                return None
            if direction == Direction.SHORT and self._btc_change_1h >= config.BTC_PUMP_THRESHOLD_PCT:
                logger.info(f"⛔ {symbol} SHORT blocked — BTC pumped {self._btc_change_1h:.2f}%")
                return None

        # ── Enhanced Volume Check ─────────────────────────
        volume_quality, volume_bonus = self._check_volume_quality(df_primary)

        # ── Calculate base score ───────────────────────────
        total_indicators = len(ALL_INDICATORS)
        agreement_ratio = len(active_votes) / total_indicators
        avg_confidence = sum(v.confidence for v in active_votes) / len(active_votes)
        base_score = agreement_ratio * avg_confidence * 100

        # Apply volume bonus/penalty
        base_score += volume_bonus

        # ── Higher timeframe confirmation ──────────────────
        confirmation_aligned = False
        confirmation_details = "No confirmation data"

        if df_confirm is not None and len(df_confirm) >= 50:
            confirm_results = [indicator_fn(df_confirm) for indicator_fn in TREND_INDICATORS]
            confirm_same = [r for r in confirm_results if r.direction == direction]

            if len(confirm_same) >= 2:
                confirmation_aligned = True
                confirmation_details = f"{config.CONFIRMATION_TIMEFRAME} trend CONFIRMED ({len(confirm_same)}/{len(TREND_INDICATORS)} aligned)"
                base_score *= config.CONFIRMATION_MULTIPLIER
            elif len(confirm_same) == 1:
                confirmation_details = f"{config.CONFIRMATION_TIMEFRAME} trend partially aligned ({len(confirm_same)}/{len(TREND_INDICATORS)})"
                base_score *= 1.1
            else:
                confirmation_details = f"{config.CONFIRMATION_TIMEFRAME} trend AGAINST signal"
                base_score *= 0.7

        # ── Extra indicators: Funding, OI, S/R ────────────
        extra_indicators: list[IndicatorResult] = []
        atr = calculate_atr(df_primary)
        if atr is None or atr == 0:
            atr = df_primary["close"].iloc[-1] * 0.01

        # Funding Rate
        funding_data = self.fetcher.fetch_funding_rate(symbol)
        funding_result = calculate_funding_rate(funding_data)
        extra_indicators.append(funding_result)

        if funding_result.direction == direction:
            base_score += funding_result.confidence * 8
        elif funding_result.direction != Direction.NEUTRAL:
            base_score -= funding_result.confidence * 5

        # Open Interest
        oi_data = self.fetcher.fetch_open_interest(symbol)
        price_change_pct = 0
        if len(df_primary) >= 10:
            old_price = df_primary["close"].iloc[-10]
            new_price = df_primary["close"].iloc[-1]
            price_change_pct = (new_price - old_price) / old_price * 100

        oi_result = calculate_open_interest(oi_data, price_change_pct)
        extra_indicators.append(oi_result)

        if oi_result.direction == direction:
            base_score += oi_result.confidence * 10
        elif oi_result.direction != Direction.NEUTRAL:
            base_score -= oi_result.confidence * 5

        # Support/Resistance
        sr_data = self.fetcher.find_support_resistance(df_primary)
        sr_result = analyze_support_resistance(sr_data, direction, atr)
        extra_indicators.append(sr_result)

        if sr_result.direction == direction:
            base_score += sr_result.confidence * 12
        elif sr_result.direction != Direction.NEUTRAL:
            base_score -= sr_result.confidence * 15

        # Cap score
        final_score = max(0, min(int(base_score), 100))

        # ── Check threshold ────────────────────────────────
        if final_score < config.SIGNAL_THRESHOLD:
            logger.debug(f"{symbol}: score {final_score} below threshold {config.SIGNAL_THRESHOLD}")
            return None

        # ── Position sizing ────────────────────────────────
        position_size_pct = self._get_position_size(final_score)

        # ── Calculate entry/SL/TP1/TP2 ─────────────────────
        current_price = float(df_primary["close"].iloc[-1])

        if direction == Direction.LONG:
            entry_low = current_price - atr * 0.3
            entry_high = current_price + atr * 0.1
            stop_loss = current_price - atr * config.ATR_SL_MULTIPLIER
            take_profit_1 = current_price + atr * config.TP1_MULTIPLIER
            take_profit_2 = current_price + atr * config.TP2_MULTIPLIER
        else:
            entry_low = current_price - atr * 0.1
            entry_high = current_price + atr * 0.3
            stop_loss = current_price + atr * config.ATR_SL_MULTIPLIER
            take_profit_1 = current_price - atr * config.TP1_MULTIPLIER
            take_profit_2 = current_price - atr * config.TP2_MULTIPLIER

        risk = abs(current_price - stop_loss)
        reward = abs(take_profit_2 - current_price)
        risk_reward = reward / risk if risk > 0 else 0

        # ── Strength label ─────────────────────────────────
        if final_score >= 90:
            strength = "🔥 Very Strong"
        elif final_score >= 80:
            strength = "💪 Strong"
        else:
            strength = "✅ Moderate"

        return Signal(
            symbol=symbol,
            direction=direction,
            score=final_score,
            strength=strength,
            current_price=current_price,
            entry_zone=(round(entry_low, 6), round(entry_high, 6)),
            stop_loss=round(stop_loss, 6),
            take_profit_1=round(take_profit_1, 6),
            take_profit_2=round(take_profit_2, 6),
            risk_reward=round(risk_reward, 2),
            position_size_pct=position_size_pct,
            primary_indicators=active_votes,
            extra_indicators=extra_indicators,
            confirmation_tf_aligned=confirmation_aligned,
            confirmation_details=confirmation_details,
            sr_levels=sr_data,
            btc_filter_info=btc_info,
            volume_quality=volume_quality,
        )

    def scan_all(self, pairs: Optional[list[str]] = None) -> list[Signal]:
        """
        Scan all configured pairs and return list of qualifying signals.
        """
        pairs = pairs or config.TRADING_PAIRS
        self.fetcher.clear_cache()
        signals = []

        # Check BTC filter first (once per cycle)
        _, btc_info = self._check_btc_filter()
        logger.info(f"🪙 {btc_info}")

        for symbol in pairs:
            try:
                signal = self.analyze_pair(symbol, btc_info)
                if signal:
                    signals.append(signal)
                    logger.info(
                        f"✅ SIGNAL: {symbol} {signal.direction.value} "
                        f"Score={signal.score} {signal.strength} "
                        f"Size={signal.position_size_pct}%"
                    )
                else:
                    logger.debug(f"⏭  {symbol}: no signal")
            except Exception as e:
                logger.error(f"Error analyzing {symbol}: {e}")

            # Small delay to avoid Bybit rate limits
            import time
            time.sleep(0.5)

        signals.sort(key=lambda s: s.score, reverse=True)
        return signals
