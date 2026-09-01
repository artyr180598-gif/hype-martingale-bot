"""Perpetual-futures derivatives analysis.

The signal engine treats derivatives as *evidence*, never as a standalone
trigger.  This module turns funding, open interest and liquidation data into a
``DerivativesSnapshot`` that the scorer can weigh.
"""

from __future__ import annotations

from v3.config import SignalConfig
from v3.models import DataBundle, DerivativesSnapshot


def _positioning(
    oi_delta: float | None,
    price_delta: float,
    funding: float | None,
    cfg: SignalConfig,
) -> tuple[str, float]:
    """Матрица OI × funding × цена: «кто и где стоит» (см. docs/IMPROVEMENTS_RESEARCH.md §3.6)."""
    if oi_delta is None:
        return "unknown", 50.0
    build = oi_delta >= cfg.OI_CHANGE_BUILD_PCT
    unwind = oi_delta <= cfg.OI_CHANGE_UNWIND_PCT
    quiet = abs(price_delta) <= cfg.POSITIONING_QUIET_PRICE_CHANGE_PCT

    if not build and not unwind:
        return "unwinding" if quiet else "unknown", 50.0

    if build:
        if price_delta > cfg.POSITIONING_QUIET_PRICE_CHANGE_PCT:
            # деньги входят, цена растёт: здоровое построение, если фандинг не перегрет
            if funding is not None and funding > cfg.FUNDING_OVERHEATED:
                return "overheated_long", 25.0
            return "healthy_long", 68.0
        if price_delta < -cfg.POSITIONING_QUIET_PRICE_CHANGE_PCT:
            # деньги входят, цена падает: перегрев лонгов или шорт-билд
            if funding is not None and funding > cfg.FUNDING_OVERHEATED * 0.5:
                return "overheated_long", 25.0
            if funding is not None and funding < 0:
                return "short_build", 65.0
            return "building", 55.0
        return "building", 55.0
    # unwind
    if price_delta < -cfg.POSITIONING_QUIET_PRICE_CHANGE_PCT:
        return "capitulation", 60.0      # закрытие лонгов = часто разворот вверх
    if price_delta > cfg.POSITIONING_QUIET_PRICE_CHANGE_PCT:
        return "short_squeeze", 45.0     # покрытие шортов = избыточный импульс
    return "unwinding", 50.0


def _liq_acceleration(bundle: DataBundle, cfg: SignalConfig, now_ms: int) -> float:
    """Сумма ликвидаций за последние ~5 минут (ускорение каскада)."""
    window = cfg.LIQ_ACCELERATION_WINDOW_SEC * 1000
    total = 0.0
    for item in bundle.liquidations:
        ts = int(item.get("ts_ms", 0) or 0)
        if ts and now_ms - ts <= window:
            total += float(item.get("size", 0) or 0)
    return total


def analyze_derivatives(bundle: DataBundle, cfg: SignalConfig) -> DerivativesSnapshot:
    funding = bundle.funding_rate
    hist = [float(x) for x in bundle.funding_history if x is not None]
    funding_trend = classify_funding(funding, hist, cfg)

    buy_liq = 0.0
    sell_liq = 0.0
    liq_count = 0
    for item in bundle.liquidations:
        size = float(item.get("size", 0) or 0)
        side = str(item.get("side", "")).lower()
        if size > 0:
            liq_count += 1
        if side in ("buy",):  # liquidated longs -> sell pressure
            buy_liq += size
        elif side in ("sell", "short"):
            sell_liq += size

    total_liq = buy_liq + sell_liq
    imbalance = (sell_liq - buy_liq) / total_liq if total_liq > 0 else 0.0
    liq_accel = _liq_acceleration(bundle, cfg, bundle.ts_ms or int(__import__("time").time() * 1000))

    score = 50.0
    if funding is not None:
        if funding > cfg.FUNDING_OVERHEATED:
            score -= 20.0
        elif funding < cfg.FUNDING_OVERBURDENED_SHORT:
            score += 15.0
        elif -cfg.FUNDING_OVERBURDENED_SHORT * 0.5 <= funding <= cfg.FUNDING_OVERHEATED * 0.5:
            score += 10.0
    if hist:
        avg = sum(hist) / len(hist)
        if funding is not None and funding > avg:
            score += 5.0
        elif funding is not None and funding < avg:
            score -= 5.0
    lsr = bundle.long_short_ratio
    if lsr is not None:
        # account ratio: >0.65 = перекос в длинные (риск ликвидаций лонгов),
        # <0.35 = перекос в короткие (потенциальный шорт-сквиз) -- контекст,
        # а не самостоятельный триггер.
        if lsr > 0.65:
            score -= 8.0
        elif lsr < 0.35:
            score += 8.0
    if total_liq > 0:
        if imbalance > 0.4 and funding is not None and funding < 0:
            score += 10.0
        elif imbalance < -0.4:
            score -= 10.0
    if liq_accel > 0:
        score -= min(10.0, liq_accel / 1e6 * 3.0)  # каскад ликвидаций = стресс

    # ── positioning-матрица (раунд 4): OI-Δ теперь реально работает ──
    oi_delta = bundle.open_interest_history[-1][1] if bundle.open_interest_history else None
    if oi_delta is None:
        oi_delta = bundle.oi_change_24h_pct
    positioning, pos_score = _positioning(oi_delta, bundle.price_24h_pct, funding, cfg)
    if positioning == "overheated_long":
        score -= 12.0
    elif positioning == "healthy_long":
        score += 8.0
    elif positioning == "short_build":
        score += 8.0
    elif positioning == "capitulation":
        score += 5.0
    elif positioning == "short_squeeze":
        score -= 5.0
    elif positioning == "building":
        score += 3.0

    note_bits: list[str] = []
    if funding is not None:
        note_bits.append(f"funding {funding * 100:.3f}%/{funding_trend}")
    if lsr is not None:
        note_bits.append(f"LS ratio {lsr:.2f}")
    if bundle.open_interest_usd:
        note_bits.append(f"OI ${bundle.open_interest_usd / 1e6:.1f}M")
        if oi_delta is not None:
            note_bits.append(f"OI Δ {oi_delta:+.1f}%")
    note_bits.append(f"positioning {positioning}")
    if total_liq > 0:
        note_bits.append(f"liqs ${total_liq / 1e6:.1f}M (imbalance {imbalance:+.2f})")

    return DerivativesSnapshot(
        funding_rate=funding,
        funding_trend=funding_trend,
        funding_history=hist[-12:],
        open_interest_usd=bundle.open_interest_usd,
        oi_change_24h_pct=round(oi_delta, 3) if oi_delta is not None else None,
        liq_buy_usd=buy_liq,
        liq_sell_usd=sell_liq,
        liq_imbalance=round(imbalance, 3),
        liq_count=liq_count,
        taker_buy_sell_ratio=lsr,
        long_short_ratio=round(lsr, 3) if lsr is not None else None,
        account_long_ratio=round(lsr, 3) if lsr is not None else None,
        mark_price=bundle.mark_price,
        index_price=bundle.index_price,
        positioning=positioning,
        positioning_score=round(pos_score, 1),
        liq_accel_usd=round(liq_accel, 2),
        score=round(min(100.0, max(0.0, score)), 1),
        note=" | ".join(note_bits),
    )


def classify_funding(rate: float | None, history: list[float], cfg: SignalConfig) -> str:
    if rate is None:
        return "unknown"
    if rate > cfg.FUNDING_OVERHEATED:
        return "overheated_long"
    if rate < cfg.FUNDING_OVERBURDENED_SHORT:
        return "overheated_short"
    if history and len(history) >= 3:
        avg = sum(history[-3:]) / len(history[-3:])
        if rate > avg + 0.0002:
            return "rising"
        if rate < avg - 0.0002:
            return "falling"
    return "neutral"
