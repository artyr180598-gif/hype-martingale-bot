"""Expected move projection — ATR, measured move, volatility expansion."""

from __future__ import annotations

import pandas as pd

from ..config import Settings


def calculate_expected_move(
    df: pd.DataFrame,
    risk_plan,
    direction: str,
    cfg: Settings,
) -> dict:
    if df.empty:
        return {"pct": 0, "atr": 0, "target_price": 0, "reason": "Нет данных"}

    try:
        last = df.iloc[-1]
        atr = float(last.get("atr", 0))
        close = float(last["close"])
        bb_width = float(last.get("bb_width", 2.0))
        adx = float(last.get("adx", 15))

        # Base expected from ATR
        base_move_pct = risk_plan.expected_move_pct if risk_plan else atr / close * 100 * cfg.ATR_TP_MULTIPLIER
        base_move_atr = risk_plan.expected_move_atr if risk_plan else cfg.ATR_TP_MULTIPLIER

        # Volatility expansion bonus
        volatility_bonus = 0
        if bb_width < 2.5:
            # Squeeze -> expansion expected larger
            volatility_bonus = 0.5

        # ADX bonus
        adx_bonus = 0
        if adx > 30:
            adx_bonus = 0.3

        expected_atr = base_move_atr + volatility_bonus + adx_bonus
        expected_pct = base_move_pct * (1 + volatility_bonus + adx_bonus * 0.5)

        # Target price
        if direction.upper() == "LONG":
            target = close * (1 + expected_pct / 100)
        else:
            target = close * (1 - expected_pct / 100)

        # Measured move from recent range
        try:
            recent_high = df["high"].tail(20).max()
            recent_low = df["low"].tail(20).min()
            range_size = recent_high - recent_low
            measured_move = range_size
            measured_pct = measured_move / close * 100 if close else 0
        except Exception:
            measured_move = 0
            measured_pct = 0

        # Combined
        combined_pct = (expected_pct + measured_pct) / 2 if measured_pct > 0 else expected_pct

        reason = (
            f"ATR {atr:.4f} ({atr/close*100:.2f}%), "
            f"BB width {bb_width:.2f}%, ADX {adx:.1f} — "
            f"ожидаемый скачок {combined_pct:.2f}% (~{expected_atr:.1f} ATR)"
        )

        return {
            "pct": combined_pct,
            "atr": expected_atr,
            "target_price": target,
            "base_pct": base_move_pct,
            "measured_pct": measured_pct,
            "reason": reason,
            "atr_value": atr,
        }
    except Exception as e:
        return {"pct": 0, "atr": 0, "target_price": 0, "reason": f"Ошибка: {e}"}
