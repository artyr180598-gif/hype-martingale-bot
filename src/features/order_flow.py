"""
Order Flow Engine — Depth Imbalances, Cumulative Volume Delta (CVD), and Liquidity Walls.
"""
from dataclasses import dataclass
from typing import Any

import pandas as pd

from src.data.models import OrderBookData


@dataclass
class LiquidityWall:
    price: float
    size_usd: float
    side: str  # "BID" or "ASK"
    multiple_of_avg: float


class OrderFlowAnalyzer:
    """
    Analyzes microstructural order flow, taker imbalances, and book depth.
    """

    @staticmethod
    def compute_cvd(df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate bar-by-bar delta and Cumulative Volume Delta (CVD).
        If taker_buy_volume is available:
            delta = 2 * taker_buy_volume - total_volume
        Otherwise approximated using high-low close location:
            delta = volume * (2 * (close - low) / (high - low) - 1)
        """
        df = df.copy()
        if "taker_buy_volume" in df.columns and (df["taker_buy_volume"] > 0).any():
            # Exact taker delta
            taker_buy = df["taker_buy_volume"].fillna(df["volume"] * 0.5)
            taker_sell = df["volume"] - taker_buy
            df["volume_delta"] = taker_buy - taker_sell
        else:
            # Approximation via intra-candle bar split
            range_hl = (df["high"] - df["low"]).replace(0, 1.0)
            delta_ratio = 2.0 * ((df["close"] - df["low"]) / range_hl) - 1.0
            df["volume_delta"] = df["volume"] * delta_ratio

        df["cvd"] = df["volume_delta"].cumsum()
        df["cvd_ema_20"] = df["cvd"].ewm(span=20, adjust=False).mean()
        df["cvd_divergence"] = df["cvd"] - df["cvd_ema_20"]
        return df

    @staticmethod
    def analyze_orderbook(ob: OrderBookData) -> dict[str, Any]:
        """
        Extract orderbook depth metrics, imbalance, and liquidity walls.
        """
        if not ob.bids or not ob.asks:
            return {
                "imbalance": 0.0,
                "spread_pct": 0.0,
                "bid_depth_usd": 0.0,
                "ask_depth_usd": 0.0,
                "liquidity_walls": [],
                "suspicious_liquidity": False,
            }

        bid_depth = ob.bid_depth_usd
        ask_depth = ob.ask_depth_usd
        imbalance = ob.orderbook_imbalance
        spread_pct = ob.spread_percent

        # Liquidity Walls detection (orders > 3.0x mean depth in top 20)
        avg_bid_size = bid_depth / len(ob.bids[:20]) if ob.bids else 1.0
        avg_ask_size = ask_depth / len(ob.asks[:20]) if ob.asks else 1.0

        walls: list[LiquidityWall] = []
        for p, s in ob.bids[:20]:
            val = p * s
            if val > avg_bid_size * 3.0:
                walls.append(LiquidityWall(price=p, size_usd=val, side="BID", multiple_of_avg=val / avg_bid_size))

        for p, s in ob.asks[:20]:
            val = p * s
            if val > avg_ask_size * 3.0:
                walls.append(LiquidityWall(price=p, size_usd=val, side="ASK", multiple_of_avg=val / avg_ask_size))

        # Suspicious liquidity behavior: extreme one-sided walls with wide spread
        suspicious = (abs(imbalance) > 0.85) and (spread_pct > 0.15)

        return {
            "imbalance": round(imbalance, 3),
            "spread_pct": round(spread_pct, 4),
            "bid_depth_usd": round(bid_depth, 2),
            "ask_depth_usd": round(ask_depth, 2),
            "liquidity_walls": walls,
            "suspicious_liquidity": suspicious,
        }
