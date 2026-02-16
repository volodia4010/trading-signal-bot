"""
ta_compat — drop-in replacement for the pandas_ta functions used by the bot.
Uses only pandas + numpy so it works on Python 3.9.
Column-naming mirrors what pandas_ta produces.
"""

import pandas as pd
import numpy as np


def ema(series: pd.Series, length: int = 20) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=length, adjust=False).mean()


def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    """Relative Strength Index."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """MACD — returns DataFrame with MACD_, MACDs_, MACDh_ columns."""
    fast_ema = ema(series, fast)
    slow_ema = ema(series, slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line

    return pd.DataFrame({
        f"MACD_{fast}_{slow}_{signal}": macd_line,
        f"MACDs_{fast}_{slow}_{signal}": signal_line,
        f"MACDh_{fast}_{slow}_{signal}": histogram,
    })


def bbands(
    series: pd.Series,
    length: int = 20,
    std: float = 2.0,
) -> pd.DataFrame:
    """Bollinger Bands — returns DataFrame with BBL_, BBM_, BBU_ columns."""
    mid = series.rolling(window=length).mean()
    rolling_std = series.rolling(window=length).std()
    upper = mid + std * rolling_std
    lower = mid - std * rolling_std

    return pd.DataFrame({
        f"BBL_{length}_{std}": lower,
        f"BBM_{length}_{std}": mid,
        f"BBU_{length}_{std}": upper,
    })


def stochrsi(
    series: pd.Series,
    length: int = 14,
    rsi_length: int = 14,
    k: int = 3,
    d: int = 3,
) -> pd.DataFrame:
    """Stochastic RSI — returns DataFrame with STOCHRSIk_ and STOCHRSId_ columns."""
    rsi_vals = rsi(series, rsi_length)

    lowest = rsi_vals.rolling(window=length).min()
    highest = rsi_vals.rolling(window=length).max()

    stoch = (rsi_vals - lowest) / (highest - lowest).replace(0, np.nan) * 100
    k_line = stoch.rolling(window=k).mean()
    d_line = k_line.rolling(window=d).mean()

    return pd.DataFrame({
        f"STOCHRSIk_{length}_{rsi_length}_{k}_{d}": k_line,
        f"STOCHRSId_{length}_{rsi_length}_{k}_{d}": d_line,
    })


def _true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """True Range helper."""
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


def atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    length: int = 14,
) -> pd.Series:
    """Average True Range."""
    tr = _true_range(high, low, close)
    return tr.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()


def adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    length: int = 14,
) -> pd.DataFrame:
    """ADX + DI — returns DataFrame with ADX_, DMP_, DMN_ columns."""
    plus_dm = high.diff().clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)

    # Zero out where the other is larger
    plus_dm[plus_dm < minus_dm] = 0
    minus_dm[minus_dm < plus_dm] = 0

    tr = _true_range(high, low, close)
    atr_vals = tr.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()

    plus_di = 100 * (plus_dm.ewm(alpha=1 / length, min_periods=length, adjust=False).mean() / atr_vals)
    minus_di = 100 * (minus_dm.ewm(alpha=1 / length, min_periods=length, adjust=False).mean() / atr_vals)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_vals = dx.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()

    return pd.DataFrame({
        f"ADX_{length}": adx_vals,
        f"DMP_{length}": plus_di,
        f"DMN_{length}": minus_di,
    })
