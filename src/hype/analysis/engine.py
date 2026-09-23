"""Main analysis engine — orchestrates indicators + scanner + confluence + risk."""

from __future__ import annotations

import pandas as pd

from ..config import Settings
from ..data.models import MarketBundle
from ..indicators import (
    compute_momentum_indicators,
    compute_structure,
    compute_trend_indicators,
    compute_volatility_indicators,
    compute_volume_indicators,
)
from ..indicators.structure import nearest_support_resistance
from ..scanner import analyze_orderbook, detect_all_signals, detect_early_impulse, detect_sweep
from .confluence import evaluate_strategies
from .confidence import calculate_bot_confidence
from .expected_move import calculate_expected_move
from .regime import detect_regime
from .risk import calculate_risk_plan, risk_score
from .scoring import calculate_quality_score


def analyze_bundle(bundle: MarketBundle, cfg: Settings, btc_bundle: MarketBundle | None = None) -> dict:
    """
    Full analysis pipeline for a MarketBundle.
    Returns dict with all computed metrics and signal.
    """
    if not bundle.has_minimum:
        return {
            "symbol": bundle.symbol,
            "exchange": bundle.exchange,
            "status": "NO_DATA",
            "reason": f"Нет данных: ticker={bundle.ticker is not None}, klines={len(bundle.klines)}",
            "errors": bundle.errors,
        }

    # Use primary TFs
    # Prefer ENTRY_TF for signals, INTERMEDIATE for structure, MACRO for trend
    entry_tf = cfg.ENTRY_TF
    inter_tf = cfg.INTERMEDIATE_TF
    macro_tf = cfg.MACRO_TF

    # Get dataframes
    df_entry = bundle.klines.get(entry_tf) or bundle.primary_candles
    df_inter = bundle.klines.get(inter_tf) or df_entry
    df_macro = bundle.klines.get(macro_tf) or df_inter
    df_1h = bundle.klines.get("1h") or df_inter

    if df_entry is None or df_entry.empty:
        return {
            "symbol": bundle.symbol,
            "status": "NO_CANDLES",
            "reason": f"Нет свечей для {entry_tf}",
        }

    # Compute indicators for each TF
    for df in [df_entry, df_inter, df_macro, df_1h]:
        if df is not None and not df.empty:
            compute_trend_indicators(df)
            compute_momentum_indicators(df)
            compute_volatility_indicators(df)
            compute_volume_indicators(df)

    # Structure from intermediate
    structure = compute_structure(df_inter)

    # Confluence on entry TF
    confluence = evaluate_strategies(df_entry, cfg)

    # Regime
    regime = detect_regime(df_inter, cfg)

    # Orderbook
    orderbook_analysis = analyze_orderbook(bundle.orderbook, cfg, price=bundle.ticker.last if bundle.ticker else None)

    # Early impulse
    early_impulse = detect_early_impulse(df_1h, cfg)

    # STOBB/SBM/JUMP signals
    stobb_signals = detect_all_signals(df_entry, cfg)

    # Sweep
    sweep = detect_sweep(df_entry, cfg)

    # Determine direction
    bias = confluence.get("bias", "NEUTRAL")
    # Adjust bias by signals
    # If SBM says LONG and confluence says NEUTRAL, favor LONG
    for sig in stobb_signals:
        if sig["type"] == "SBM":
            bias = sig["direction"]
            break

    # If still neutral, check structure + trend
    if bias == "NEUTRAL":
        if structure["structure"] == "TRENDING_UP" and confluence["long_score"] > 55:
            bias = "LONG"
        elif structure["structure"] == "TRENDING_DOWN" and confluence["short_score"] > 55:
            bias = "SHORT"

    # If still neutral, NO TRADE
    if bias == "NEUTRAL":
        return {
            "symbol": bundle.symbol,
            "exchange": bundle.exchange,
            "status": "NO_TRADE",
            "reason": "Конфликт таймфреймов / слабый консенсус — нет направленного сигнала",
            "confluence": confluence,
            "regime": regime,
            "structure": structure,
            "orderbook": orderbook_analysis,
            "early_impulse": early_impulse,
            "signals": stobb_signals,
            "sweep": sweep,
            "ticker": bundle.ticker,
        }

    # Risk plan
    entry_price = bundle.ticker.last if bundle.ticker else float(df_entry["close"].iloc[-1])
    atr = float(df_entry["atr"].iloc[-1]) if "atr" in df_entry.columns else entry_price * 0.01

    support, resistance = nearest_support_resistance(
        entry_price, structure.get("support", []), structure.get("resistance", [])
    )

    risk_plan = calculate_risk_plan(
        entry=entry_price,
        atr=atr,
        direction=bias,
        cfg=cfg,
        support=support,
        resistance=resistance,
        swing_low=structure.get("last_swing_low"),
        swing_high=structure.get("last_swing_high"),
        account_balance=1000.0,  # default, will be overridden by user settings
    )

    # Risk score
    r_score = risk_score(risk_plan, cfg, spread_pct=bundle.ticker.spread_pct if bundle.ticker else None)

    # Quality score
    quality = calculate_quality_score(
        confluence=confluence,
        regime=regime,
        orderbook_analysis=orderbook_analysis,
        risk_plan=risk_plan,
        signals=stobb_signals,
        early_impulse=early_impulse,
        cfg=cfg,
    )

    # Data completeness
    data_completeness = 0.0
    # Count TFs present
    tf_count = len(bundle.klines)
    data_completeness += min(1.0, tf_count / len(cfg.timeframes)) * 0.5
    # Ticker + orderbook
    if bundle.ticker:
        data_completeness += 0.25
    if bundle.orderbook:
        data_completeness += 0.15
    if bundle.funding:
        data_completeness += 0.10

    # Timeframe agreement (how many TFs agree with bias)
    tf_agreement = 0
    tf_total = 0
    for tf, df in bundle.klines.items():
        if df.empty or len(df) < 20:
            continue
        tf_total += 1
        # Quick check: price vs EMA50
        try:
            if bias == "LONG" and float(df["close"].iloc[-1]) > float(df.get("ema50", df["close"]).iloc[-1]):
                tf_agreement += 1
            elif bias == "SHORT" and float(df["close"].iloc[-1]) < float(df.get("ema50", df["close"]).iloc[-1]):
                tf_agreement += 1
        except Exception:
            pass
    tf_agreement_ratio = tf_agreement / tf_total if tf_total else 0.5

    # Volume/orderbook score
    vol_score = 50
    try:
        last = df_entry.iloc[-1]
        vol_ratio = float(last.get("volume_ratio", 1.0))
        imbalance = orderbook_analysis.get("imbalance", 0.5)
        liq = orderbook_analysis.get("liquidity_score", 50)
        vol_score = min(100, vol_ratio * 20 + liq * 0.5)
        # Adjust for imbalance in favor of bias
        if bias == "LONG" and imbalance > 0.6:
            vol_score += 10
        elif bias == "SHORT" and imbalance < 0.4:
            vol_score += 10
        vol_score = max(0, min(100, vol_score))
    except Exception:
        vol_score = 50

    # Impulse score from early_impulse
    impulse_score = early_impulse.get("heat", 0)

    # Bot confidence
    confidence_info = calculate_bot_confidence(
        quality_score=quality["score"],
        data_completeness=data_completeness,
        timeframe_agreement=tf_agreement_ratio,
        volume_orderbook_score=vol_score,
        risk_score=r_score,
        rr=risk_plan.risk_reward,
        impulse_score=impulse_score,
        cfg=cfg,
    )

    # Expected move
    expected = calculate_expected_move(df_entry, risk_plan, bias, cfg)

    # Final decision: check gates
    no_trade_reasons = []

    if quality["score"] < cfg.QUALITY_MIN:
        no_trade_reasons.append(f"Quality {quality['score']:.0f} < min {cfg.QUALITY_MIN}")

    if confidence_info["confidence"] / 100 < cfg.CONFIDENCE_MIN:
        no_trade_reasons.append(f"Confidence {confidence_info['confidence']:.0f}% < min {cfg.CONFIDENCE_MIN*100:.0f}%")

    if r_score > cfg.MAX_RISK_SCORE_TO_ENTER:
        no_trade_reasons.append(f"Risk score {r_score} > max {cfg.MAX_RISK_SCORE_TO_ENTER}")

    if risk_plan.risk_reward < cfg.MIN_RISK_REWARD:
        # Allow reversal exception
        is_reversal = structure.get("choch") is not None or sweep is not None
        min_rr = cfg.MIN_RISK_REWARD_REVERSAL if is_reversal else cfg.MIN_RISK_REWARD
        if risk_plan.risk_reward < min_rr:
            no_trade_reasons.append(f"R:R {risk_plan.risk_reward:.2f} < min {min_rr}")

    if bundle.data_age_seconds > cfg.MAX_DATA_AGE_SECONDS:
        no_trade_reasons.append(f"Data stale {bundle.data_age_seconds:.0f}s > {cfg.MAX_DATA_AGE_SECONDS}s")

    # Entry extension filter (don't chase)
    try:
        vwap = float(df_entry["vwap"].iloc[-1])
        price_vs_vwap_atr = abs(float(df_entry["price_vs_vwap_atr"].iloc[-1])) if "price_vs_vwap_atr" in df_entry.columns else 0
        if price_vs_vwap_atr > cfg.ENTRY_MAX_EXTENSION_ATR and cfg.ENTRY_MAX_EXTENSION_ATR > 0:
            no_trade_reasons.append(f"Цена ушла от VWAP {price_vs_vwap_atr:.1f} ATR > {cfg.ENTRY_MAX_EXTENSION_ATR} — не догоняем")
    except Exception:
        pass

    if no_trade_reasons and quality["score"] < cfg.SCAN_LIST_QUALITY_MIN:
        # Still return but mark NO_TRADE
        status = "NO_TRADE"
    elif no_trade_reasons:
        status = "NO_TRADE"
    else:
        status = "SIGNAL"

    return {
        "symbol": bundle.symbol,
        "exchange": bundle.exchange,
        "status": status,
        "direction": bias,
        "entry": entry_price,
        "ticker": bundle.ticker,
        "risk_plan": risk_plan,
        "risk_score": r_score,
        "quality": quality,
        "confidence": confidence_info,
        "confluence": confluence,
        "regime": regime,
        "structure": structure,
        "orderbook": orderbook_analysis,
        "early_impulse": early_impulse,
        "signals": stobb_signals,
        "sweep": sweep,
        "expected_move": expected,
        "data_completeness": data_completeness,
        "tf_agreement": tf_agreement_ratio,
        "vol_score": vol_score,
        "no_trade_reasons": no_trade_reasons,
        "bundle": bundle,
        "timestamp": bundle.timestamp,
    }
