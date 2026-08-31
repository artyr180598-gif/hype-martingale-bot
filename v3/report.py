"""Telegram/chat rendering for v3 signals.

Two modes:
  * BEGINNER -- clear action, levels, risk, invalidation, explanation;
  * PRO      -- full factor breakdown, indicators, derivatives, order flow,
                market regime, quantitative metrics.

Every report shows the **data timestamp** and a stale-data warning so a signal
is never presented as fresh when the underlying feed is old.
"""

from __future__ import annotations

import time

from v3.models import TradingSignal

EMOJI = {"LONG": "🚨 LONG", "SHORT": "🚨 SHORT", "WAIT": "⏸ WAIT", "NO_TRADE": "⛔ NO TRADE"}


def _stamp(signal: TradingSignal) -> str:
    when = "?"
    if signal.ts_ms:
        when = time.strftime("%H:%M:%S UTC", time.gmtime(signal.ts_ms / 1000.0))
    age = signal.data_age_seconds
    age_bit = f" · возраст {age:.0f}с" if age is not None else ""
    return f"🕐 Данные: {when}{age_bit}"


def _stale_line(signal: TradingSignal) -> list[str]:
    if signal.stale:
        return [
            "",
            "⚠️ **DATA STALE** — данные устарели, сигнал НЕ является актуальным.",
        ]
    return []


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
        _stamp(signal),
        f"📊 Signal Quality: {signal.quality:.0f}/100 (тир {signal.tier})",
        f"📈 Режим: {signal.regime} | Горизонт: {signal.horizon}",
        "",
        "## 💰 TRADE PLAN",
        f"Вход: {signal.entry_zone[0]:.8g} … {signal.entry_zone[1]:.8g}",
        f"🛑 Stop Loss: {signal.stop_loss:.8g}",
        "🎯 Take Profit:",
    ]
    for i, t in enumerate(signal.targets[:3], 1):
        lines.append(f"  TP{i}: {t:.8g}")
    lines.extend([
        f"⚖️ R:R 1:{signal.rr:.2f} | Риск {signal.risk_score}/10 | Плечо ≤ {signal.leverage}x",
        "## 📈 ПОЧЕМУ",
    ])
    for r in signal.reasons[:6]:
        lines.append(f"  • {r}")
    lines.extend(["", "## ⚠️ РИСКИ & ИНВАЛИДАЦИЯ"])
    for r in signal.risks[:5]:
        lines.append(f"  • {r}")
    lines.extend([
        "",
        f"🚨 Инвалидация: {signal.invalidation}",
        "",
        "❓ Непонятные термины (ATR, BOS, funding)? Жми «📚 ПОМОЩЬ».",
        "❗ Это аналитический сигнал, а не гарантия результата.",
    ])
    lines.extend(_stale_line(signal))
    return "\n".join(lines)


def render_pro(signal: TradingSignal) -> str:
    if signal.direction in ("WAIT", "NO_TRADE"):
        return render_no_trade(signal, pro=True)
    lines = [
        f"{EMOJI.get(signal.direction, '⚠️ SIGNAL')} SIGNAL [PRO]",
        "",
        f"🪙 {signal.symbol} | {signal.market}",
        _stamp(signal),
        f"Price {signal.price:.8g} | confidence {signal.confidence:.2f} | Signal Quality {signal.quality:.1f}/100",
        f"Regime {signal.regime} | risk {signal.risk_score}/10 | R:R 1:{signal.rr:.2f}",
        "",
        "## 📊 Score breakdown",
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
    if der.get("long_short_ratio") is not None:
        lines.append(f"  long/short accounts {float(der['long_short_ratio']):.2f} (0..1)")
    if der.get("mark_price") is not None:
        lines.append(f"  mark {der['mark_price']} | index {der.get('index_price')}")

    of = signal.features.get("orderflow", {})
    lines.extend([
        "",
        "🌊 Order flow:",
        f"  book imbalance {of.get('imbalance', 0):+.2f}",
        f"  spread {of.get('spread_pct')}% | grade {of.get('liquidity_grade')}",
        f"  CVD trend {of.get('cvd_trend', 0):+.2f}",
    ])
    news = signal.features.get("news") or []
    if news:
        lines.extend(["", "🗞 News (source + timestamp):"])
        for n in news[:3]:
            when = time.strftime("%H:%M UTC", time.gmtime(n.get("ts_ms", 0) / 1000.0)) if n.get("ts_ms") else "?"
            lines.append(f"  • {n.get('title', '')[:90]} [{n.get('source', '?')} {when}] s={n.get('sentiment', 0):+.2f}")
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
        "## 🧾 RISK BRIEF",
    ])
    rb = signal.risk_brief
    lines.extend([
        f"  Риск: ${rb.risk_usd:.2f} | Позиция ${rb.position_usd:.2f} ({rb.position_pct:.2f}%)",
        f"  Плечо ≤ {rb.leverage}x | Маржа ${rb.margin_usd:.2f}",
        f"  Ликвидация (изолир.): {rb.liquidation_price or 'н/д'}",
    ])
    lines.extend([
        "",
        "❗ Статистический сигнал, не гарантия прибыли. Signal Quality ≠ вероятность прибыли.",
    ])
    lines.extend(_stale_line(signal))
    return "\n".join(lines)


def render_no_trade(signal: TradingSignal, pro: bool = False) -> str:
    lines = [f"{EMOJI.get(signal.direction, '⛔')} NO TRADE", "", f"🪙 {signal.symbol}", _stamp(signal)]
    if pro:
        lines.append(f"Regime {signal.regime} | Signal Quality {signal.quality:.1f}/100 | risk {signal.risk_score}/10")
    lines.extend(["", "**Причины NO TRADE:**"])
    for r in signal.no_trade_reasons[:8]:
        lines.append(f"  • {r}")
    if signal.reasons:
        lines.extend(["", "Наблюдения:"])
        for r in signal.reasons[:5]:
            lines.append(f"  • {r}")
    lines.extend([
        "",
        "✅ Сейчас лучше не входить. Ждём подтверждённый сетап.",
        "❗ Это аналитический сигнал, а не гарантия результата.",
    ])
    lines.extend(_stale_line(signal))
    return "\n".join(lines)
