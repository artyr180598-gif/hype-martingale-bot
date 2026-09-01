"""Telegram/chat rendering for v3 signals.

Two modes:
  * BEGINNER -- человеческий язык: что купить/продать, где выход, почему,
    сколько рискуем. Никаких внутренних переменных движка.
  * PRO      -- полный факторный разбор, индикаторы, деривативы, order flow,
    market regime, количественные метрики (для опытных).

Every report shows the **data source + timestamp** and a stale-data warning so
a signal is never presented as fresh when the underlying feed is old.
"""

from __future__ import annotations

import time

from v3.config import SignalConfig
from v3.models import TradingSignal
from v3.tg.render import plain_reasons, quality_label

EMOJI = {"LONG": "🟢 LONG", "SHORT": "🔻 SHORT", "WAIT": "⏸ WAIT", "NO_TRADE": "⛔ NO TRADE"}

_SOURCE_NAMES = {
    "bybit": "Bybit v5",
    "binance": "Binance",
    "binance+coingecko": "Binance + CoinGecko",
    "mexc": "MEXC",
}

_DISCLAIMER = "❗ Это аналитика, а не гарантия результата. Шорт/плечо — повышенный риск."


def _source_name(source: str) -> str:
    name = _SOURCE_NAMES.get((source or "").lower())
    return name or (source or "источник данных")


def _stamp(signal: TradingSignal) -> str:
    when = "?"
    if signal.ts_ms:
        when = time.strftime("%H:%M:%S", time.gmtime(signal.ts_ms / 1000.0))
    src = _source_name(signal.source)
    age = ""
    if signal.data_age_seconds is not None:
        age = f" · возраст {signal.data_age_seconds:.0f}с"
    return f"📡 {src} · обновлено {when} UTC{age}"


def _stale_line(signal: TradingSignal) -> list[str]:
    if signal.stale:
        return [
            "",
            "⚠️ **ДАННЫЕ УСТАРЕЛИ** — сигнал НЕ является актуальным.",
            "❗ Это статистический сигнал, не гарантия прибыли.",
        ]
    return []


def _emergence_lines(signal: TradingSignal) -> list[str]:
    """«⚡ Похоже, движение только начинается» — если ignition выше порога (ранний отбор)."""
    e = (signal.features or {}).get("emergence") or {}
    if not e:
        return []
    ignition = float(e.get("ignition", 0.0) or 0.0)
    threshold = SignalConfig().EMERGENCE_IGNITION_MIN
    if ignition < threshold:
        return []
    direction = str(e.get("early_direction", "FLAT"))
    d = {"LONG": "вверх", "SHORT": "вниз", "FLAT": "пока неясно"}.get(direction, "пока неясно")
    notes = [n for n in e.get("notes", []) if n][:3]
    lines = [
        "",
        f"⚡ **Похоже, движение только начинается** (подогрев {ignition:.0f} из 100)",
        f"• Возможное направление: {d} — это подсказка, а не команда.",
    ]
    for n in notes:
        lines.append(f"• {n}")
    lines.append(
        "• Что это значит: бот заметил ранние признаки (объём, сжатие, позиции), "
        "но вход — только после подтверждения движком; гарантии движения нет."
    )
    return lines


def render_signal(signal: TradingSignal, mode: str = "beginner") -> str:
    if mode.lower() == "pro":
        return render_pro(signal)
    return render_beginner(signal)


def render_beginner(signal: TradingSignal) -> str:
    """Карточка понятным языком: что купить, оценка сделки, почему, что делать."""
    if signal.direction in ("WAIT", "NO_TRADE"):
        return render_no_trade(signal)
    rb = signal.risk_brief
    lines = [
        f"{EMOJI.get(signal.direction, '⚠️ SIGNAL')} — {signal.symbol}",
        "",
        _stamp(signal),
        "",
        f"⭐ Оценка сетапа: {quality_label(signal.quality, signal.tier)}",
        f"Уверенность в данных: {signal.confidence:.1f}/1",
        f"📈 Рынок: {signal.regime} | горизонт: {signal.horizon}",
    ]
    lines += _emergence_lines(signal)
    lines += [
        "",
        "**Что делать:**",
    ]
    buy_or_short = "Купить (ставка на рост)" if signal.direction == "LONG" else "Продать в шорт (ставка на падение)"
    entry_low, entry_high = signal.entry_zone
    entry_mid = (entry_low + entry_high) / 2 if entry_high else signal.price
    lines.append(f"• {buy_or_short} {signal.symbol} в диапазоне {entry_low:.8g}–{entry_high:.8g}")
    stop_pct = abs(entry_mid - signal.stop_loss) / entry_mid * 100 if entry_mid and signal.stop_loss else 0.0
    stop_side = "упадёт ниже" if signal.direction == "LONG" else "выйдет выше"
    lines.append(
        f"• Стоп-лосс: {signal.stop_loss:.8g} (примерно −{stop_pct:.1f}% от входа) — "
        f"если цена {stop_side}, выходим, идея отменена"
    )
    targets = _targets_human(signal, entry_mid)
    if targets:
        lines.append(f"• Цели: {targets}")
    risk_pct, deposit = _risk_pct(signal)
    lev = f"плечо до {signal.leverage}x" if signal.leverage else ""
    risk_part = f"риск ≈ {risk_pct:.1f}% депозита (${rb.risk_usd:.1f})" if risk_pct is not None else ""
    if rb and rb.liquidation_price:
        risk_part += (", " if risk_part else "") + f"ликвидация (изолированная): {rb.liquidation_price:.8g}"
    tail = " · ".join(p for p in (lev, risk_part) if p)
    if tail:
        lines.append(f"• {tail}")

    why = plain_reasons(signal)
    if why:
        lines += ["", "**Почему:**"]
        for r in why:
            lines.append(f"• {r}")

    if signal.risks:
        human_risks = [r for r in signal.risks if not r.startswith(("stop distance", "priority"))][:3]
        if human_risks:
            lines += ["", "**На что обратить внимание:**"]
            for r in human_risks:
                lines.append(f"• {r}")

    lines += [
        "",
        "❗ Оценка — качество сетапа, а не вероятность прибыли.",
        _DISCLAIMER,
        "❓ Непонятные термины? Жми «📚 ПОМОЩЬ».",
    ]
    lines.extend(_stale_line(signal))
    return "\n".join(lines)


def _targets_human(signal: TradingSignal, entry_mid: float) -> str:
    """Форматирует цели словами с процентом от входа."""
    if not signal.targets or not entry_mid:
        return ""
    out = []
    for i, t in enumerate(signal.targets[:3], 1):
        if signal.direction == "LONG":
            pct = (t / entry_mid - 1.0) * 100.0
        else:
            pct = (1.0 - t / entry_mid) * 100.0
        out.append(f"{t:.8g} ({pct:+.1f}%)")
    return " → ".join(out)


def _risk_pct(signal: TradingSignal) -> tuple[float | None, float | None]:
    """Вернуть (риск % депозита, депозит) если возможно вывести из risk brief."""
    rb = signal.risk_brief
    if rb and rb.position_usd > 0 and rb.position_pct > 0:
        deposit = rb.position_usd * 100.0 / rb.position_pct
        if deposit > 0:
            return round(rb.risk_usd / deposit * 100.0, 2), deposit
    if rb and rb.max_deposit_pct > 0:
        return round(rb.max_deposit_pct, 2), None
    return None, None


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
    ]
    if signal.scenario:
        scenario_names = {
            "trend": "тренд",
            "reversal_choch": "CHoCH-разворот",
            "liquidity_sweep": "liquidity sweep",
            "range_reversion": "mean-reversion в диапазоне",
            "breakout_watch": "условный пробой",
        }
        lines.append(f"Scenario: {scenario_names.get(signal.scenario, signal.scenario)}")
    if signal.condition:
        lines.append(f"Condition: {signal.condition}")
    e = (signal.features or {}).get("emergence") or {}
    if e:
        lines.append(
            f"⚡ Emergence {e.get('ignition', 0):.0f}/100 | RVOL {e.get('rvol', 1):.2f} | "
            f"squeeze_release {e.get('squeeze_release')} | consol {e.get('consolidation')} | "
            f"dpos {e.get('dpos', 0.5):.2f} | hint {e.get('early_direction')}"
        )
    lines.extend([
        "",
        "## 📊 Score breakdown",
    ])
    for f in signal.score_breakdown.factors:
        lines.append(f"  {f.name:<18} {f.value:6.1f}/{f.weight:.0f}")
    for name, val in signal.score_breakdown.penalties.items():
        lines.append(f"  penalty {name}: -{val:.1f}")
    lines.extend(["", "🧭 Timeframes:"])

    for v in signal.features.get("timeframes", []):
        extra = ""
        if v.get("mfi") is not None:
            extra += f" MFI {v.get('mfi', 0):.0f}"
        if v.get("rvol", 1.0) != 1.0:
            extra += f" RVOL {v.get('rvol', 1.0):.2f}"
        if v.get("ema_stack", 0) != 0:
            extra += f" EMA-stack {v.get('ema_stack', 0):+d}"
        if v.get("macd_cross", 0):
            extra += f" MACD×{'↑' if v.get('macd_cross') > 0 else '↓'}"
        lines.append(
            f"  {v['timeframe']:<4} {v['trend']:<5} ADX {v['adx']:.0f} RSI {v['rsi']:.0f} "
            f"ATR {v['atr_pct']:.2f}% vol_z {v['vol_z']:+.2f}{extra}"
        )

    der = signal.features.get("derivatives", {})
    liq_note = "н/д"
    liq_buy = der.get("liq_buy_usd") or 0
    liq_sell = der.get("liq_sell_usd") or 0
    if liq_buy or liq_sell:
        liq_note = f"buy ${liq_buy / 1e3:.1f}k | sell ${liq_sell / 1e3:.1f}k"
    lines.extend([
        "",
        "📉 Derivatives:",
        f"  funding {der.get('funding_rate')} / {der.get('funding_trend')}",
        f"  OI ${(der.get('open_interest_usd') or 0) / 1e6:.1f}M",
        f"  OI Δ {der.get('oi_change_24h_pct')}% | positioning {der.get('positioning', 'unknown')} ({der.get('positioning_score', 50):.0f}/100)",
        f"  liq {liq_note} | imbalance {der.get('liq_imbalance', 0):+.2f} | accel ${(der.get('liq_accel_usd') or 0) / 1e3:.1f}k/5m",
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
        f"  liquidation (isolated): {rb.liquidation_price or 'н/д'}",
    ])
    lines.extend([
        "",
        "❗ Статистический сигнал, не гарантия прибыли. Signal Quality ≠ вероятность прибыли.",
    ])
    lines.extend(_stale_line(signal))
    return "\n".join(lines)


def render_no_trade(signal: TradingSignal, pro: bool = False) -> str:
    lines = [f"{EMOJI.get(signal.direction, '⛔ NO TRADE')} — ВХОД ЗАПРЕЩЁН", "", f"🪙 {signal.symbol}", _stamp(signal)]
    if pro:
        lines.append(f"Regime {signal.regime} | Signal Quality {signal.quality:.1f}/100 | risk {signal.risk_score}/10")
    else:
        lines.append(f"Оценка сетапа: {quality_label(signal.quality, signal.tier)}")
    if signal.no_trade_reasons:
        lines.extend(["", "**Почему нет входа:**"])
        for r in signal.no_trade_reasons[:8]:
            lines.append(f"• {r}")
    if signal.reasons:
        lines.extend(["", "Наблюдения:"])
        for r in signal.reasons[:5]:
            lines.append(f"• {r}")
    lines.extend([
        "",
        "✅ Сейчас лучше не входить. Даже если цена пойдёт без вас — чистого сетапа нет.",
        "❗ Это аналитический сигнал, а не гарантия результата.",
    ])
    lines.extend(_stale_line(signal))
    return "\n".join(lines)
