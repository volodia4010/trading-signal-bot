"""
Data fetcher module — wraps CCXT to fetch OHLCV candle data from Binance.
"""

import ccxt
import pandas as pd
import config
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class DataFetcher:
    """Fetches OHLCV data from Binance Futures via CCXT."""

    def __init__(self):
        exchange_class = getattr(ccxt, config.EXCHANGE_ID)
        self.exchange = exchange_class({
            "enableRateLimit": True,
            "options": {
                "defaultType": "future",
            },
        })
        self._cache: dict[str, pd.DataFrame] = {}
        logger.info(f"DataFetcher initialized for {config.EXCHANGE_ID} futures")

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int = config.CANDLE_LIMIT,
    ) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV candles for a symbol/timeframe.
        Returns a pandas DataFrame with columns:
            timestamp, open, high, low, close, volume
        Uses in-memory cache to avoid redundant calls within the same scan cycle.
        """
        cache_key = f"{symbol}_{timeframe}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        try:
            raw = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            if not raw:
                logger.warning(f"No data returned for {symbol} {timeframe}")
                return None

            df = pd.DataFrame(
                raw,
                columns=["timestamp", "open", "high", "low", "close", "volume"],
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df.set_index("timestamp", inplace=True)

            # Ensure numeric types
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = df[col].astype(float)

            self._cache[cache_key] = df
            logger.debug(f"Fetched {len(df)} candles for {symbol} {timeframe}")
            return df

        except ccxt.NetworkError as e:
            logger.error(f"Network error fetching {symbol}: {e}")
            return None
        except ccxt.ExchangeError as e:
            logger.error(f"Exchange error fetching {symbol}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error fetching {symbol}: {e}")
            return None

    def clear_cache(self):
        """Clear the data cache (call at the start of each scan cycle)."""
        self._cache.clear()

    def get_current_price(self, symbol: str) -> Optional[float]:
        """Get the current price for a symbol."""
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            return float(ticker["last"])
        except Exception as e:
            logger.error(f"Error fetching price for {symbol}: {e}")
            return None

    def fetch_funding_rate(self, symbol: str) -> Optional[dict]:
        """
        Fetch current funding rate for a futures symbol via CCXT (Bybit).
        """
        cache_key = f"{symbol}_funding"
        if cache_key in self._cache:
            return self._cache[cache_key]

        try:
            # CCXT unified method — works with Bybit and most exchanges
            funding = self.exchange.fetch_funding_rate(symbol)

            result = {
                "funding_rate": float(funding.get("fundingRate", 0) or 0),
                "mark_price": float(funding.get("markPrice", 0) or 0),
                "index_price": float(funding.get("indexPrice", 0) or 0),
                "next_funding_time": int(funding.get("fundingTimestamp", 0) or 0),
            }

            self._cache[cache_key] = result
            logger.debug(f"Funding rate for {symbol}: {result['funding_rate']:.6f}")
            return result

        except Exception as e:
            logger.error(f"Error fetching funding rate for {symbol}: {e}")
            return None

    def fetch_open_interest(self, symbol: str) -> Optional[dict]:
        """
        Fetch open interest for a futures symbol via CCXT (Bybit).
        """
        cache_key = f"{symbol}_oi"
        if cache_key in self._cache:
            return self._cache[cache_key]

        try:
            # CCXT unified method for open interest
            oi = self.exchange.fetch_open_interest(symbol)
            oi_coins = float(oi.get("openInterestAmount", 0) or 0)

            # Try to get OI history for trend analysis
            oi_values = []
            try:
                oi_history = self.exchange.fetch_open_interest_history(
                    symbol, timeframe="5m", limit=30
                )
                oi_values = [
                    float(h.get("openInterestValue", 0) or h.get("openInterestAmount", 0) or 0)
                    for h in oi_history
                ]
            except Exception:
                # OI history not available — use single point
                if oi_coins > 0:
                    oi_values = [oi_coins]

            result = {
                "open_interest": oi_coins,
                "oi_values": oi_values,
            }

            self._cache[cache_key] = result
            logger.debug(f"OI for {symbol}: {oi_coins}")
            return result

        except Exception as e:
            logger.error(f"Error fetching OI for {symbol}: {e}")
            return None

    def find_support_resistance(
        self, df: pd.DataFrame, window: int = 20, num_levels: int = 5
    ) -> dict:
        """
        Detect support and resistance levels from price data using pivot points.
        Returns dict with 'support' and 'resistance' lists of (price, strength) tuples.
        """
        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values

        resistance_levels = []
        support_levels = []

        # Find pivot highs and lows
        for i in range(window, len(df) - window):
            # Pivot high — local maximum
            if highs[i] == max(highs[i - window:i + window + 1]):
                resistance_levels.append(float(highs[i]))
            # Pivot low — local minimum
            if lows[i] == min(lows[i - window:i + window + 1]):
                support_levels.append(float(lows[i]))

        # Cluster nearby levels (within 0.5% of each other)
        def cluster_levels(levels: list[float], threshold_pct: float = 0.5) -> list[tuple[float, int]]:
            if not levels:
                return []
            levels.sort()
            clusters = []
            current_cluster = [levels[0]]

            for i in range(1, len(levels)):
                if (levels[i] - current_cluster[0]) / current_cluster[0] * 100 < threshold_pct:
                    current_cluster.append(levels[i])
                else:
                    avg = sum(current_cluster) / len(current_cluster)
                    clusters.append((round(avg, 6), len(current_cluster)))
                    current_cluster = [levels[i]]

            avg = sum(current_cluster) / len(current_cluster)
            clusters.append((round(avg, 6), len(current_cluster)))

            # Sort by strength (number of touches) descending
            clusters.sort(key=lambda x: x[1], reverse=True)
            return clusters[:num_levels]

        current_price = float(closes[-1])

        # Cluster and separate into support (below price) and resistance (above price)
        all_supports = cluster_levels(support_levels)
        all_resistances = cluster_levels(resistance_levels)

        # Filter: supports below current price, resistances above
        supports = [(p, s) for p, s in all_supports if p < current_price]
        resistances = [(p, s) for p, s in all_resistances if p > current_price]

        return {
            "support": supports,
            "resistance": resistances,
            "current_price": current_price,
        }

