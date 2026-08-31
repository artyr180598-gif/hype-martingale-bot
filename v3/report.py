"""Telegram/chat rendering for v3 signals.

Two modes:
  * BEGINNER -- clear action, levels, risk, invalidation, explanation;
  * PRO      -- full factor breakdown, indicators, derivatives, order flow,
                market regime, quantitative metrics.
"""

from __future__ import annotations

from v3.models import TradingSignal

EMOJI = {"LONG": "🚨 LONG", "SHORT": "🚨 SHORT", "WAIT": "⏸ WAIT", "NO_TRADE": "⛔ NO TRADE"}


def render_signal(signal: TradingSignal, mode: str = "beginner") -> str:
    if mode.lower() == "pro":
        return render_pro(signal)
    return render_beginner(signal)


def render_beginner(signal: TradingSignal) -> str:
    if signal.direction in ("WAIT", "NO_TRADE"):
        return render_no_trade(signal)
    lines = [
        f"{EMOJI.get(signal.direction, '⚠️ SIGNAL')} SIGNAL",
        "",
        f"🪙 {signal.symbol}",
        f"📊 Сила сигнала: {signal.quality:.0f}/100 (тир {signal.tier})",
        "",
        "💰 Вход:",
        f"  {signal.entry_zone[0]:.8g} … {signal.entry_zone[1]:.8g}",
        f"🛑 Stop Loss: {signal.stop_loss:.8g}",
        "🎯 Take Profit:",
    ]
    for i, t in enumerate(signal.targets[:3], 1):
        lines.append(f"  TP{i}: {t:.8g}")
    lines.extend([
        "",
        f"⚖️ R:R 1:{signal.rr:.2f} | Риск {signal.risk_score}/10 | Плечо ≤ {signal.leverage}x",
        f"⏱ Горизонт: {signal.horizon} | Режим: {signal.regime}",
        "",
        "📈 Почему:",
    ])
    for r in signal.reasons[:6]:
        lines.append(f"  • {r}")
    lines.extend(["", "⚠️ Риски:"])
    for r in signal.risks[:5]:
        lines.append(f"  • {r}")
    lines.extend([
        "",
        f"🚨 Инвалидация: {signal.invalidation}",
        "",
        "❗ Это аналитический сигнал, а не гарантия результата.",
    ])
    return "\n".join(lines)


def render_pro(signal: TradingSignal) -> str:
    if signal.direction in ("WAIT", "NO_TRADE"):
        return render_no_trade(signal, pro=True)
    lines = [
        f"{EMOJI.get(signal.direction, '⚠️ SIGNAL')} SIGNAL [PRO]",
        "",
        f"🪙 {signal.symbol} | {signal.market}",
        f"Price {signal.price:.8g} | confidence {signal.confidence:.2f} | quality {signal.quality:.1f}/100",
        f"Regime {signal.regime} | risk {signal.risk_score}/10 | R:R 1:{signal.rr:.2f}",
        "",
        "📊 Score breakdown:",
    ]
    for f in signal.score_breakdown.factors:
        lines.append(f"  {f.name:<18} {f.value:6.1f}/{f.weight:.0f}")
    for name, val in signal.score_breakdown.penalties.items():
        lines.append(f"  penalty {name}: -{val:.1f}")
    lines.extend(["", "🧭 Timeframes:"])

    for v in signal.features.get("timeframes", []):
        lines.append(
            f"  {v['timeframe']:<4} {v['trend']:<5} ADX {v['adx']:.0f} RSI {v['rsi']:.0f} "
            f"ATR {v['atr_pct']:.2f}% vol_z {v['vol_z']:+.2f}"
        )

    der = signal.features.get("derivatives", {})
    lines.extend([
        "",
        "📉 Derivatives:",
        f"  funding {der.get('funding_rate')} / {der.get('funding_trend')}",
        f"  OI ${(der.get('open_interest_usd') or 0) / 1e6:.1f}M",
        f"  liq buy ${(der.get('liq_buy_usd') or 0) / 1e3:.1f}k | sell ${(der.get('liq_sell_usd') or 0) / 1e3:.1f}k",
    ])

    of = signal.features.get("orderflow", {})
    lines.extend([
        "",
        "🌊 Order flow:",
        f"  book imbalance {of.get('imbalance', 0):+.2f}",
        f"  spread {of.get('spread_pct')}% | grade {of.get('liquidity_grade')}",
        f"  CVD trend {of.get('cvd_trend', 0):+.2f}",
    ])
    lines.extend([
        "",
        "💰 Levels:",
        f"  Entry {signal.entry_zone[0]:.8g}-{signal.entry_zone[1]:.8g}",
        f"  SL {signal.stop_loss:.8g} | TPs {', '.join(f'{t:.8g}' for t in signal.targets)}",
        f"  Invalidation {signal.invalidation}",
    ])
    rb = signal.risk_brief
    lines.extend([
        "",
        "🧾 Risk brief:",
        f"  risk_usd ${rb.risk_usd:.2f} | position ${rb.position_usd:.2f} ({rb.position_pct:.2f}%)",
        f"  leverage {rb.leverage}x | margin ${rb.margin_usd:.2f}",
    ])
    lines.extend([
        "",
        "❗ Статистический сигнал, не гарантия прибыли.",
    ])
    return "\n".join(lines)


def render_no_trade(signal: TradingSignal, pro: bool = False) -> str:
    lines = [f"{EMOJI.get(signal.direction, '⛔')} NO TRADE", "", f"🪙 {signal.symbol}"]
    if pro:
        lines.append(f"Regime {signal.regime} | quality {signal.quality:.1f}/100 | risk {signal.risk_score}/10")
    lines.extend(["", "Причины:"])
    for r in signal.no_trade_reasons[:8]:
        lines.append(f"  • {r}")
    if signal.reasons:
        lines.extend(["", "Наблюдения:"])
        for r in signal.reasons[:5]:
            lines.append(f"  • {r}")
    lines.extend(["", "✅ Сейчас лучше не входить. Ждём подтверждённый сетап."])
    lines.append("❗ Это аналитический сигнал, а не гарантия результата.")
    return "\n".join(lines)
