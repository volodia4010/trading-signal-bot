"""
Technical indicator calculations.
Each indicator returns an IndicatorResult with direction, confidence, and description.
"""

import pandas as pd
import ta_compat as ta  # pure-pandas reimplementation (Python 3.9 compat)
import config
from dataclasses import dataclass
from enum import Enum
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class Direction(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


@dataclass
class IndicatorResult:
    """Result of a single indicator analysis."""
    name: str
    direction: Direction
    confidence: float        # 0.0 – 1.0
    description: str


def calculate_ema_cross(df: pd.DataFrame) -> IndicatorResult:
    """
    EMA Crossover (fast/slow).
    LONG  — fast EMA crosses above slow EMA
    SHORT — fast EMA crosses below slow EMA
    """
    name = "EMA Cross"
    try:
        fast = ta.ema(df["close"], length=config.EMA_FAST)
        slow = ta.ema(df["close"], length=config.EMA_SLOW)

        if fast is None or slow is None or len(fast) < 2:
            return IndicatorResult(name, Direction.NEUTRAL, 0.0, "Insufficient data")

        curr_fast, prev_fast = fast.iloc[-1], fast.iloc[-2]
        curr_slow, prev_slow = slow.iloc[-1], slow.iloc[-2]

        # Bullish crossover
        if prev_fast <= prev_slow and curr_fast > curr_slow:
            gap_pct = abs(curr_fast - curr_slow) / curr_slow * 100
            conf = min(gap_pct / 0.5, 1.0)
            return IndicatorResult(name, Direction.LONG, conf,
                                   f"EMA{config.EMA_FAST} crossed above EMA{config.EMA_SLOW}")

        # Bearish crossover
        if prev_fast >= prev_slow and curr_fast < curr_slow:
            gap_pct = abs(curr_fast - curr_slow) / curr_slow * 100
            conf = min(gap_pct / 0.5, 1.0)
            return IndicatorResult(name, Direction.SHORT, conf,
                                   f"EMA{config.EMA_FAST} crossed below EMA{config.EMA_SLOW}")

        # Trend continuation (no crossover but clear separation)
        if curr_fast > curr_slow:
            gap_pct = (curr_fast - curr_slow) / curr_slow * 100
            if gap_pct > 0.3:
                return IndicatorResult(name, Direction.LONG, min(gap_pct / 1.0, 0.6),
                                       f"EMA{config.EMA_FAST} above EMA{config.EMA_SLOW} (trending)")
        elif curr_fast < curr_slow:
            gap_pct = (curr_slow - curr_fast) / curr_slow * 100
            if gap_pct > 0.3:
                return IndicatorResult(name, Direction.SHORT, min(gap_pct / 1.0, 0.6),
                                       f"EMA{config.EMA_FAST} below EMA{config.EMA_SLOW} (trending)")

        return IndicatorResult(name, Direction.NEUTRAL, 0.0, "EMAs intertwined — no clear signal")

    except Exception as e:
        logger.error(f"EMA Cross error: {e}")
        return IndicatorResult(name, Direction.NEUTRAL, 0.0, f"Error: {e}")


def calculate_rsi(df: pd.DataFrame) -> IndicatorResult:
    """
    RSI — Relative Strength Index.
    LONG  — RSI crosses above oversold level (recovery from oversold)
    SHORT — RSI crosses below overbought level (rejection from overbought)
    """
    name = "RSI"
    try:
        rsi = ta.rsi(df["close"], length=config.RSI_PERIOD)
        if rsi is None or len(rsi) < 2:
            return IndicatorResult(name, Direction.NEUTRAL, 0.0, "Insufficient data")

        curr, prev = rsi.iloc[-1], rsi.iloc[-2]

        # Bullish: crossing above oversold
        if prev <= config.RSI_OVERSOLD and curr > config.RSI_OVERSOLD:
            conf = min((config.RSI_OVERSOLD - (prev + curr) / 2 + 10) / 20, 1.0)
            return IndicatorResult(name, Direction.LONG, max(conf, 0.5),
                                   f"RSI recovering from oversold ({curr:.1f})")

        # Bearish: crossing below overbought
        if prev >= config.RSI_OVERBOUGHT and curr < config.RSI_OVERBOUGHT:
            conf = min(((prev + curr) / 2 - config.RSI_OVERBOUGHT + 10) / 20, 1.0)
            return IndicatorResult(name, Direction.SHORT, max(conf, 0.5),
                                   f"RSI rejected from overbought ({curr:.1f})")

        # Deep oversold zone
        if curr < config.RSI_OVERSOLD:
            conf = min((config.RSI_OVERSOLD - curr) / 15, 0.7)
            return IndicatorResult(name, Direction.LONG, conf,
                                   f"RSI in oversold zone ({curr:.1f})")

        # Deep overbought zone
        if curr > config.RSI_OVERBOUGHT:
            conf = min((curr - config.RSI_OVERBOUGHT) / 15, 0.7)
            return IndicatorResult(name, Direction.SHORT, conf,
                                   f"RSI in overbought zone ({curr:.1f})")

        return IndicatorResult(name, Direction.NEUTRAL, 0.0,
                               f"RSI neutral ({curr:.1f})")

    except Exception as e:
        logger.error(f"RSI error: {e}")
        return IndicatorResult(name, Direction.NEUTRAL, 0.0, f"Error: {e}")


def calculate_macd(df: pd.DataFrame) -> IndicatorResult:
    """
    MACD — Moving Average Convergence Divergence.
    LONG  — MACD line crosses above signal line
    SHORT — MACD line crosses below signal line
    """
    name = "MACD"
    try:
        macd_df = ta.macd(df["close"],
                          fast=config.MACD_FAST,
                          slow=config.MACD_SLOW,
                          signal=config.MACD_SIGNAL)
        if macd_df is None or len(macd_df) < 2:
            return IndicatorResult(name, Direction.NEUTRAL, 0.0, "Insufficient data")

        macd_col = f"MACD_{config.MACD_FAST}_{config.MACD_SLOW}_{config.MACD_SIGNAL}"
        signal_col = f"MACDs_{config.MACD_FAST}_{config.MACD_SLOW}_{config.MACD_SIGNAL}"
        hist_col = f"MACDh_{config.MACD_FAST}_{config.MACD_SLOW}_{config.MACD_SIGNAL}"

        curr_macd = macd_df[macd_col].iloc[-1]
        prev_macd = macd_df[macd_col].iloc[-2]
        curr_signal = macd_df[signal_col].iloc[-1]
        prev_signal = macd_df[signal_col].iloc[-2]
        curr_hist = macd_df[hist_col].iloc[-1]
        prev_hist = macd_df[hist_col].iloc[-2]

        # Bullish crossover
        if prev_macd <= prev_signal and curr_macd > curr_signal:
            conf = min(abs(curr_hist) / (abs(curr_macd) + 1e-10) * 2, 1.0)
            return IndicatorResult(name, Direction.LONG, max(conf, 0.6),
                                   "MACD bullish crossover")

        # Bearish crossover
        if prev_macd >= prev_signal and curr_macd < curr_signal:
            conf = min(abs(curr_hist) / (abs(curr_macd) + 1e-10) * 2, 1.0)
            return IndicatorResult(name, Direction.SHORT, max(conf, 0.6),
                                   "MACD bearish crossover")

        # Histogram growing (momentum increasing)
        if curr_hist > 0 and curr_hist > prev_hist:
            return IndicatorResult(name, Direction.LONG, 0.4,
                                   "MACD histogram growing (bullish momentum)")
        if curr_hist < 0 and curr_hist < prev_hist:
            return IndicatorResult(name, Direction.SHORT, 0.4,
                                   "MACD histogram falling (bearish momentum)")

        return IndicatorResult(name, Direction.NEUTRAL, 0.0,
                               "MACD no clear signal")

    except Exception as e:
        logger.error(f"MACD error: {e}")
        return IndicatorResult(name, Direction.NEUTRAL, 0.0, f"Error: {e}")


def calculate_bollinger_bands(df: pd.DataFrame) -> IndicatorResult:
    """
    Bollinger Bands.
    LONG  — price bounces off lower band
    SHORT — price rejected from upper band
    """
    name = "Bollinger Bands"
    try:
        bb = ta.bbands(df["close"], length=config.BB_PERIOD, std=config.BB_STD)
        if bb is None or len(bb) < 2:
            return IndicatorResult(name, Direction.NEUTRAL, 0.0, "Insufficient data")

        # Dynamic column lookup (pandas-ta versions vary in naming)
        upper_col = [c for c in bb.columns if c.startswith("BBU_")][0]
        lower_col = [c for c in bb.columns if c.startswith("BBL_")][0]
        mid_col = [c for c in bb.columns if c.startswith("BBM_")][0]

        close = df["close"].iloc[-1]
        prev_close = df["close"].iloc[-2]
        upper = bb[upper_col].iloc[-1]
        lower = bb[lower_col].iloc[-1]
        mid = bb[mid_col].iloc[-1]
        prev_lower = bb[lower_col].iloc[-2]
        prev_upper = bb[upper_col].iloc[-2]

        band_width = upper - lower
        if band_width == 0:
            return IndicatorResult(name, Direction.NEUTRAL, 0.0, "Zero bandwidth")

        # Bounce off lower band
        if prev_close <= prev_lower and close > lower:
            position = (close - lower) / band_width
            conf = min(position * 2, 1.0)
            return IndicatorResult(name, Direction.LONG, max(conf, 0.6),
                                   f"Price bouncing off lower BB")

        # Rejection from upper band
        if prev_close >= prev_upper and close < upper:
            position = (upper - close) / band_width
            conf = min(position * 2, 1.0)
            return IndicatorResult(name, Direction.SHORT, max(conf, 0.6),
                                   f"Price rejected from upper BB")

        # Price below lower band (extreme oversold)
        if close < lower:
            distance = (lower - close) / band_width
            return IndicatorResult(name, Direction.LONG, min(distance * 3, 0.8),
                                   f"Price below lower BB (oversold)")

        # Price above upper band (extreme overbought)
        if close > upper:
            distance = (close - upper) / band_width
            return IndicatorResult(name, Direction.SHORT, min(distance * 3, 0.8),
                                   f"Price above upper BB (overbought)")

        return IndicatorResult(name, Direction.NEUTRAL, 0.0,
                               "Price within Bollinger Bands")

    except Exception as e:
        logger.error(f"Bollinger Bands error: {e}")
        return IndicatorResult(name, Direction.NEUTRAL, 0.0, f"Error: {e}")


def calculate_stochastic_rsi(df: pd.DataFrame) -> IndicatorResult:
    """
    Stochastic RSI.
    LONG  — %K crosses above %D in oversold zone
    SHORT — %K crosses below %D in overbought zone
    """
    name = "Stoch RSI"
    try:
        stoch = ta.stochrsi(df["close"],
                            length=config.STOCH_RSI_PERIOD,
                            rsi_length=config.STOCH_RSI_PERIOD,
                            k=config.STOCH_RSI_K,
                            d=config.STOCH_RSI_D)
        if stoch is None or len(stoch) < 2:
            return IndicatorResult(name, Direction.NEUTRAL, 0.0, "Insufficient data")

        k_col = f"STOCHRSIk_{config.STOCH_RSI_PERIOD}_{config.STOCH_RSI_PERIOD}_{config.STOCH_RSI_K}_{config.STOCH_RSI_D}"
        d_col = f"STOCHRSId_{config.STOCH_RSI_PERIOD}_{config.STOCH_RSI_PERIOD}_{config.STOCH_RSI_K}_{config.STOCH_RSI_D}"

        curr_k = stoch[k_col].iloc[-1]
        prev_k = stoch[k_col].iloc[-2]
        curr_d = stoch[d_col].iloc[-1]
        prev_d = stoch[d_col].iloc[-2]

        # Bullish crossover in oversold zone
        if (prev_k <= prev_d and curr_k > curr_d and
                curr_k < config.STOCH_RSI_OVERSOLD + 10):
            conf = min((config.STOCH_RSI_OVERSOLD + 10 - curr_k) / 20, 1.0)
            return IndicatorResult(name, Direction.LONG, max(conf, 0.6),
                                   f"StochRSI bullish crossover in oversold ({curr_k:.0f})")

        # Bearish crossover in overbought zone
        if (prev_k >= prev_d and curr_k < curr_d and
                curr_k > config.STOCH_RSI_OVERBOUGHT - 10):
            conf = min((curr_k - config.STOCH_RSI_OVERBOUGHT + 10) / 20, 1.0)
            return IndicatorResult(name, Direction.SHORT, max(conf, 0.6),
                                   f"StochRSI bearish crossover in overbought ({curr_k:.0f})")

        # Deep oversold
        if curr_k < config.STOCH_RSI_OVERSOLD:
            return IndicatorResult(name, Direction.LONG, 0.4,
                                   f"StochRSI oversold ({curr_k:.0f})")

        # Deep overbought
        if curr_k > config.STOCH_RSI_OVERBOUGHT:
            return IndicatorResult(name, Direction.SHORT, 0.4,
                                   f"StochRSI overbought ({curr_k:.0f})")

        return IndicatorResult(name, Direction.NEUTRAL, 0.0,
                               f"StochRSI neutral ({curr_k:.0f})")

    except Exception as e:
        logger.error(f"Stoch RSI error: {e}")
        return IndicatorResult(name, Direction.NEUTRAL, 0.0, f"Error: {e}")


def calculate_adx(df: pd.DataFrame) -> IndicatorResult:
    """
    ADX + Directional Indicators (DI+ / DI-).
    LONG  — ADX > threshold and +DI > -DI
    SHORT — ADX > threshold and -DI > +DI
    """
    name = "ADX"
    try:
        adx_df = ta.adx(df["high"], df["low"], df["close"], length=config.ADX_PERIOD)
        if adx_df is None or len(adx_df) < 1:
            return IndicatorResult(name, Direction.NEUTRAL, 0.0, "Insufficient data")

        adx_col = f"ADX_{config.ADX_PERIOD}"
        dmp_col = f"DMP_{config.ADX_PERIOD}"
        dmn_col = f"DMN_{config.ADX_PERIOD}"

        adx_val = adx_df[adx_col].iloc[-1]
        plus_di = adx_df[dmp_col].iloc[-1]
        minus_di = adx_df[dmn_col].iloc[-1]

        if adx_val < config.ADX_THRESHOLD:
            return IndicatorResult(name, Direction.NEUTRAL, 0.0,
                                   f"ADX weak trend ({adx_val:.1f} < {config.ADX_THRESHOLD})")

        # Strong trend detected
        conf = min((adx_val - config.ADX_THRESHOLD) / 25, 1.0)

        if plus_di > minus_di:
            di_gap = plus_di - minus_di
            conf = min(conf + di_gap / 30, 1.0)
            return IndicatorResult(name, Direction.LONG, conf,
                                   f"ADX {adx_val:.0f} bullish (+DI {plus_di:.0f} > -DI {minus_di:.0f})")
        else:
            di_gap = minus_di - plus_di
            conf = min(conf + di_gap / 30, 1.0)
            return IndicatorResult(name, Direction.SHORT, conf,
                                   f"ADX {adx_val:.0f} bearish (-DI {minus_di:.0f} > +DI {plus_di:.0f})")

    except Exception as e:
        logger.error(f"ADX error: {e}")
        return IndicatorResult(name, Direction.NEUTRAL, 0.0, f"Error: {e}")


def calculate_volume(df: pd.DataFrame) -> IndicatorResult:
    """
    Volume analysis.
    Confirms signals when current volume is significantly above average.
    """
    name = "Volume"
    try:
        vol = df["volume"]
        if len(vol) < 20:
            return IndicatorResult(name, Direction.NEUTRAL, 0.0, "Insufficient data")

        curr_vol = vol.iloc[-1]
        avg_vol = vol.iloc[-20:].mean()

        if avg_vol == 0:
            return IndicatorResult(name, Direction.NEUTRAL, 0.0, "Zero average volume")

        vol_ratio = curr_vol / avg_vol

        if vol_ratio >= config.VOLUME_MULTIPLIER:
            # Volume spike detected — direction determined by price action
            price_change = df["close"].iloc[-1] - df["close"].iloc[-2]
            conf = min((vol_ratio - 1.0) / 2.0, 1.0)

            if price_change > 0:
                return IndicatorResult(name, Direction.LONG, conf,
                                       f"Volume spike {vol_ratio:.1f}x (bullish)")
            elif price_change < 0:
                return IndicatorResult(name, Direction.SHORT, conf,
                                       f"Volume spike {vol_ratio:.1f}x (bearish)")
            else:
                return IndicatorResult(name, Direction.NEUTRAL, conf,
                                       f"Volume spike {vol_ratio:.1f}x (neutral price)")

        return IndicatorResult(name, Direction.NEUTRAL, 0.0,
                               f"Normal volume ({vol_ratio:.1f}x avg)")

    except Exception as e:
        logger.error(f"Volume error: {e}")
        return IndicatorResult(name, Direction.NEUTRAL, 0.0, f"Error: {e}")


def calculate_atr(df: pd.DataFrame) -> Optional[float]:
    """Calculate ATR for stop-loss / take-profit levels."""
    try:
        atr = ta.atr(df["high"], df["low"], df["close"], length=config.ATR_PERIOD)
        if atr is not None and len(atr) > 0:
            return float(atr.iloc[-1])
    except Exception as e:
        logger.error(f"ATR error: {e}")
    return None


def calculate_funding_rate(funding_data: dict) -> IndicatorResult:
    """
    Funding Rate analysis.
    Extreme positive funding → too many longs → SHORT signal (likely reversal).
    Extreme negative funding → too many shorts → LONG signal (likely reversal).
    """
    name = "Funding Rate"
    try:
        if not funding_data:
            return IndicatorResult(name, Direction.NEUTRAL, 0.0, "No funding data")

        rate = funding_data["funding_rate"]

        # Extreme positive funding (> 0.05% = 0.0005) — too many longs
        if rate > 0.001:  # > 0.1%
            conf = min((rate - 0.001) / 0.002, 1.0)
            return IndicatorResult(name, Direction.SHORT, max(conf, 0.7),
                                   f"Extreme positive funding ({rate*100:.4f}%) — shorts squeezed out")
        elif rate > 0.0005:  # > 0.05%
            conf = min((rate - 0.0005) / 0.001, 0.7)
            return IndicatorResult(name, Direction.SHORT, max(conf, 0.4),
                                   f"High positive funding ({rate*100:.4f}%) — longs overcrowded")

        # Extreme negative funding (< -0.05%) — too many shorts
        elif rate < -0.001:  # < -0.1%
            conf = min((abs(rate) - 0.001) / 0.002, 1.0)
            return IndicatorResult(name, Direction.LONG, max(conf, 0.7),
                                   f"Extreme negative funding ({rate*100:.4f}%) — longs squeezed out")
        elif rate < -0.0005:  # < -0.05%
            conf = min((abs(rate) - 0.0005) / 0.001, 0.7)
            return IndicatorResult(name, Direction.LONG, max(conf, 0.4),
                                   f"High negative funding ({rate*100:.4f}%) — shorts overcrowded")

        return IndicatorResult(name, Direction.NEUTRAL, 0.0,
                               f"Funding neutral ({rate*100:.4f}%)")

    except Exception as e:
        logger.error(f"Funding rate error: {e}")
        return IndicatorResult(name, Direction.NEUTRAL, 0.0, f"Error: {e}")


def calculate_open_interest(oi_data: dict, price_change_pct: float) -> IndicatorResult:
    """
    Open Interest trend analysis.
    OI rising + price rising = strong bullish (new money entering longs)
    OI rising + price falling = strong bearish (new money entering shorts)
    OI falling + price rising = weak rally (short covering)
    OI falling + price falling = weak selloff (long liquidation)
    """
    name = "Open Interest"
    try:
        if not oi_data or not oi_data.get("oi_values") or len(oi_data["oi_values"]) < 10:
            return IndicatorResult(name, Direction.NEUTRAL, 0.0, "No OI data")

        values = oi_data["oi_values"]
        recent_avg = sum(values[-5:]) / 5
        older_avg = sum(values[:5]) / 5

        if older_avg == 0:
            return IndicatorResult(name, Direction.NEUTRAL, 0.0, "Zero OI")

        oi_change_pct = (recent_avg - older_avg) / older_avg * 100

        # OI rising significantly (> 3%)
        if oi_change_pct > 3:
            conf = min(oi_change_pct / 10, 1.0)
            if price_change_pct > 0:
                return IndicatorResult(name, Direction.LONG, max(conf, 0.6),
                                       f"OI rising +{oi_change_pct:.1f}% + price up → strong bullish")
            elif price_change_pct < 0:
                return IndicatorResult(name, Direction.SHORT, max(conf, 0.6),
                                       f"OI rising +{oi_change_pct:.1f}% + price down → strong bearish")

        # OI falling significantly (< -3%)
        elif oi_change_pct < -3:
            conf = min(abs(oi_change_pct) / 10, 0.5)  # Lower confidence for OI drops
            if price_change_pct > 0:
                return IndicatorResult(name, Direction.NEUTRAL, 0.3,
                                       f"OI falling {oi_change_pct:.1f}% + price up → weak rally (covering)")
            elif price_change_pct < 0:
                return IndicatorResult(name, Direction.NEUTRAL, 0.3,
                                       f"OI falling {oi_change_pct:.1f}% + price down → liquidations")

        return IndicatorResult(name, Direction.NEUTRAL, 0.0,
                               f"OI stable ({oi_change_pct:+.1f}%)")

    except Exception as e:
        logger.error(f"OI error: {e}")
        return IndicatorResult(name, Direction.NEUTRAL, 0.0, f"Error: {e}")


def analyze_support_resistance(
    sr_data: dict, direction: Direction, atr: float
) -> IndicatorResult:
    """
    Check if a signal direction conflicts with nearby S/R levels.
    Warns if trying to go LONG near strong resistance or SHORT near strong support.
    Also boosts confidence if price is bouncing off a support (LONG) or resistance (SHORT).
    """
    name = "S/R Levels"
    try:
        if not sr_data:
            return IndicatorResult(name, Direction.NEUTRAL, 0.0, "No S/R data")

        price = sr_data["current_price"]
        supports = sr_data.get("support", [])
        resistances = sr_data.get("resistance", [])
        proximity_threshold = atr * 1.0  # Within 1 ATR of a level

        # Check nearest resistance
        if resistances:
            nearest_res_price, nearest_res_strength = min(resistances, key=lambda x: x[0] - price)
            dist_to_res = nearest_res_price - price

            if direction == Direction.LONG and dist_to_res < proximity_threshold:
                conf = min(nearest_res_strength / 3, 0.8)
                return IndicatorResult(name, Direction.SHORT, conf,
                                       f"⚠️ Resistance at {nearest_res_price:.2f} ({nearest_res_strength} touches) within {dist_to_res:.0f}")

        # Check nearest support
        if supports:
            nearest_sup_price, nearest_sup_strength = max(supports, key=lambda x: x[0])
            dist_to_sup = price - nearest_sup_price

            if direction == Direction.SHORT and dist_to_sup < proximity_threshold:
                conf = min(nearest_sup_strength / 3, 0.8)
                return IndicatorResult(name, Direction.LONG, conf,
                                       f"⚠️ Support at {nearest_sup_price:.2f} ({nearest_sup_strength} touches) within {dist_to_sup:.0f}")

        # Price near support + LONG = confirming
        if supports and direction == Direction.LONG:
            nearest_sup_price, nearest_sup_strength = max(supports, key=lambda x: x[0])
            dist_to_sup = price - nearest_sup_price
            if dist_to_sup < proximity_threshold * 2:
                conf = min(nearest_sup_strength / 4, 0.6)
                return IndicatorResult(name, Direction.LONG, conf,
                                       f"Near support {nearest_sup_price:.2f} ({nearest_sup_strength}x) — bounce zone")

        # Price near resistance + SHORT = confirming
        if resistances and direction == Direction.SHORT:
            nearest_res_price, nearest_res_strength = min(resistances, key=lambda x: x[0] - price)
            dist_to_res = nearest_res_price - price
            if dist_to_res < proximity_threshold * 2:
                conf = min(nearest_res_strength / 4, 0.6)
                return IndicatorResult(name, Direction.SHORT, conf,
                                       f"Near resistance {nearest_res_price:.2f} ({nearest_res_strength}x) — rejection zone")

        return IndicatorResult(name, Direction.NEUTRAL, 0.0,
                               "Price away from key S/R levels")

    except Exception as e:
        logger.error(f"S/R error: {e}")
        return IndicatorResult(name, Direction.NEUTRAL, 0.0, f"Error: {e}")


# Standard indicator functions (take only DataFrame)
ALL_INDICATORS = [
    calculate_ema_cross,
    calculate_rsi,
    calculate_macd,
    calculate_bollinger_bands,
    calculate_stochastic_rsi,
    calculate_adx,
    calculate_volume,
]

# Trend indicators for higher-timeframe confirmation
TREND_INDICATORS = [
    calculate_ema_cross,
    calculate_macd,
    calculate_adx,
]
