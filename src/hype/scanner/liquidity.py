"""Liquidity / Orderflow analysis — walls, imbalance, sweeps (ported from liquidity AI bot)."""

from __future__ import annotations

from ..config import Settings
from ..data.models import Orderbook
import pandas as pd


def analyze_orderbook(ob: Orderbook | None, cfg: Settings, price: float | None = None) -> dict:
    if not ob:
        return {
            "imbalance": 0.5,
            "imbalance_signal": "NEUTRAL",
            "walls_bids": [],
            "walls_asks": [],
            "wall_signal": "NONE",
            "depth_bids_usd": 0,
            "depth_asks_usd": 0,
            "spread_pct": None,
            "liquidity_score": 50,
        }

    try:
        imbalance = ob.imbalance()  # 0..1
        if imbalance >= cfg.ORDERBOOK_IMBALANCE_BULL:
            imb_signal = "BULLISH"
        elif imbalance <= cfg.ORDERBOOK_IMBALANCE_BEAR:
            imb_signal = "BEARISH"
        else:
            imb_signal = "NEUTRAL"

        walls = ob.find_walls(threshold_usd=cfg.LIQUIDITY_WALL_USD)
        walls_bids = walls["bids"]
        walls_asks = walls["asks"]

        # Wall signal: large walls near price can act as support/resistance
        wall_signal = "NONE"
        if walls_bids and not walls_asks:
            wall_signal = "BID_WALL_SUPPORT"
        elif walls_asks and not walls_bids:
            wall_signal = "ASK_WALL_RESISTANCE"
        elif walls_bids and walls_asks:
            # Compare total
            bid_wall_usd = sum(w.price * w.qty for w in walls_bids)
            ask_wall_usd = sum(w.price * w.qty for w in walls_asks)
            if bid_wall_usd > ask_wall_usd * 1.5:
                wall_signal = "BID_DOMINANT"
            elif ask_wall_usd > bid_wall_usd * 1.5:
                wall_signal = "ASK_DOMINANT"

        depth_bids = ob.depth_usd("bids", 20)
        depth_asks = ob.depth_usd("asks", 20)
        spread = ob.spread_pct

        # Liquidity score 0-100
        total_depth = depth_bids + depth_asks
        liquidity_score = min(100, total_depth / 1_000_000 * 20)  # 5M = 100

        return {
            "imbalance": imbalance,
            "imbalance_signal": imb_signal,
            "walls_bids": [{"price": w.price, "qty": w.qty, "usd": w.price * w.qty} for w in walls_bids[:5]],
            "walls_asks": [{"price": w.price, "qty": w.qty, "usd": w.price * w.qty} for w in walls_asks[:5]],
            "wall_signal": wall_signal,
            "depth_bids_usd": depth_bids,
            "depth_asks_usd": depth_asks,
            "spread_pct": spread,
            "liquidity_score": liquidity_score,
        }
    except Exception as e:
        return {
            "imbalance": 0.5,
            "imbalance_signal": "NEUTRAL",
            "walls_bids": [],
            "walls_asks": [],
            "wall_signal": f"ERROR {e}",
            "depth_bids_usd": 0,
            "depth_asks_usd": 0,
            "spread_pct": None,
            "liquidity_score": 50,
        }


def detect_sweep(df: pd.DataFrame, cfg: Settings) -> dict | None:
    """
    Detect liquidity sweep: price wicks beyond recent high/low then reverses.
    Indicates stop hunt.
    """
    if df.empty or len(df) < 20:
        return None

    try:
        last = df.iloc[-1]
        atr = float(last.get("atr", last["close"] * 0.01))
        lookback = 20
        recent_high = df["high"].tail(lookback).max()
        recent_low = df["low"].tail(lookback).min()

        close = float(last["close"])
        high = float(last["high"])
        low = float(last["low"])

        # Bullish sweep: low dips below recent low then closes above
        if low < recent_low and close > recent_low:
            sweep_dist = (recent_low - low) / atr if atr else 0
            if sweep_dist >= 0.5:
                return {
                    "type": "BULLISH_SWEEP",
                    "direction": "LONG",
                    "level": recent_low,
                    "distance_atr": sweep_dist,
                    "reason": f"Bullish sweep: wicked {sweep_dist:.2f} ATR below {recent_low:.4f} then reclaimed",
                }

        # Bearish sweep
        if high > recent_high and close < recent_high:
            sweep_dist = (high - recent_high) / atr if atr else 0
            if sweep_dist >= 0.5:
                return {
                    "type": "BEARISH_SWEEP",
                    "direction": "SHORT",
                    "level": recent_high,
                    "distance_atr": sweep_dist,
                    "reason": f"Bearish sweep: wicked {sweep_dist:.2f} ATR above {recent_high:.4f} then rejected",
                }
    except Exception:
        pass
    return None
