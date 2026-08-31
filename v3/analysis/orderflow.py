"""Order-book and microstructure analysis.

The exchange API exposes public depth and trade snapshots, not true level-2
order flow.  We therefore use a conservative, labelled proxy: book imbalance,
walls, spread and cumulative-volume-delta direction.  Every proxy is reported
as a feature, never as a guaranteed "institutional flow" signal.
"""

from __future__ import annotations

from typing import Any

from v3.config import SignalConfig
from v3.models import OrderFlowSnapshot, TimeframeView


def analyze_orderflow(
    orderbook: dict[str, Any] | None,
    view: TimeframeView | None,
    cfg: SignalConfig,
    notional_usd: float = 5_000.0,
) -> OrderFlowSnapshot:
    if not orderbook:
        return OrderFlowSnapshot(
            liquidity_grade="empty",
            note="order book unavailable",
        )

    bids = [(float(p), float(q)) for p, q in (orderbook.get("bids") or []) if p and q]
    asks = [(float(p), float(q)) for p, q in (orderbook.get("asks") or []) if p and q]
    if not bids or not asks:
        return OrderFlowSnapshot(liquidity_grade="empty", note="empty order book")

    best_bid = max(bids, key=lambda x: x[0])
    best_ask = min(asks, key=lambda x: x[0])
    mid = (best_bid[0] + best_ask[0]) / 2.0
    spread = best_ask[0] - best_bid[0]
    spread_pct = spread / mid * 100.0 if mid else 0.0

    bid_depth = sum(p * q for p, q in bids if p >= mid * (1 - 0.01))
    ask_depth = sum(p * q for p, q in asks if p <= mid * (1 + 0.01))
    total = bid_depth + ask_depth
    imbalance = (bid_depth - ask_depth) / total if total > 0 else 0.0

    biggest_bid = max((p * q for p, q in bids), default=0.0)
    biggest_ask = max((p * q for p, q in asks), default=0.0)

    # conservative slippage estimate for a market order of `notional_usd`
    remaining = float(notional_usd)
    fill_qty = 0.0
    fill_usd = 0.0
    for p, q in asks:
        if remaining <= 0:
            break
        take_usd = min(p * q, remaining)
        take_qty = take_usd / p if p > 0 else 0.0
        fill_qty += take_qty
        fill_usd += take_usd
        remaining -= take_usd
    avg_price = fill_usd / fill_qty if fill_qty > 0 else 0.0
    slippage = (avg_price - mid) / mid * 100.0 if mid else None

    if slippage is not None and slippage <= 0.15 and total >= 10 * notional_usd:
        grade = "excellent"
    elif slippage is not None and slippage <= 0.6:
        grade = "ok"
    elif slippage is not None and slippage <= 2.0:
        grade = "thin"
    else:
        grade = "empty"

    cvd_trend = view.cvd_trend if view is not None else 0.0
    vol_imb = float(cvd_trend) / 3.0 if abs(cvd_trend) < 10 else (1.0 if cvd_trend > 0 else -1.0)

    score = 50.0
    if grade in ("excellent", "ok"):
        score += 20.0
    elif grade == "thin":
        score += 5.0
    else:
        score -= 15.0
    if spread_pct > cfg.MAX_SPREAD_PCT:
        score -= 20.0
    elif spread_pct <= cfg.MAX_SPREAD_PCT * 0.3:
        score += 5.0
    if imbalance > 0.3:
        score += 10.0
    elif imbalance < -0.3:
        score -= 10.0

    return OrderFlowSnapshot(
        spread_pct=round(spread_pct, 4),
        bid_depth_usd=round(bid_depth, 2),
        ask_depth_usd=round(ask_depth, 2),
        imbalance=round(imbalance, 3),
        biggest_bid_wall_usd=round(biggest_bid, 2),
        biggest_ask_wall_usd=round(biggest_ask, 2),
        liquidity_grade=grade,
        slippage_pct=round(slippage, 4) if slippage is not None else None,
        cvd_trend=round(cvd_trend, 3),
        volume_imbalance=round(vol_imb, 3),
        score=round(min(100.0, max(0.0, score)), 1),
        note=f"depth ${bid_depth / 1e3:.0f}k/${ask_depth / 1e3:.0f}k, imbalance {imbalance:+.2f}, spread {spread_pct:.3f}%",
    )



