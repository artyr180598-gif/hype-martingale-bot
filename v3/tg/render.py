"""Telegram renderers: compact rows, market overview, settings, glossary.

The full analysis card lives in ``v3/report.py``; this module renders *lists*
and *explainers* used by the interactive platform UI.

Beginner-facing rules (жёстко):
  * никаких внутренних переменных движка (heat/adx/vol_z/trend_score/...) —
    только слова и понятные числа;
  * «Оценка сетапа» — качество сетапа, а не вероятность прибыли. Никаких
    формулировок вида «шанс 72%»;
  * у каждого вывода — реальный источник данных и timestamp.
"""

from __future__ import annotations

import time
from typing import Any

from v3.config import SignalConfig
from v3.models import TradingSignal

QUALITY_LEGEND = (
    "Шкала оценки сетапов:\n"
    "  S 82–100 — отличный\n"
    "  A 72–81 — хороший\n"
    "  B 62–71 — средний, нужна дисциплина\n"
    "  C 50–61 — слабый, обычно не входим\n"
    "  ниже 50 — не входим\n"
    "Оценка — это качество сетапа, а не вероятность прибыли."
)

# ── glossary ────────────────────────────────────────────────────
GLOSSARY: dict[str, str] = {
    "rsi": "RSI (индекс относительной силы) — шкала 0..100. Высокий RSI у сильного тренда "
           "это не «пора продавать», а признак силы. В HYPE он используется в контексте "
           "тренда/структуры, а не как отдельный сигнал.",
    "atr": "ATR (средний истинный диапазон) — насколько цена в среднем двигается за бар. "
           "Из него система считает стоп-лосс: стоп дальше при высокой волатильности.",
    "adx": "ADX — сила тренда (0..100). <22 — тренд слабый; >25 — выраженный тренд. "
           "Направление показывают +DI/-DI.",
    "bos": "BOS (Break of Structure) — цена пробила предыдущий экстремум в направлении тренда; "
           "CHoCH (Change of Character) — первый разворотный пробой. HYPE считает их по свингам.",
    "funding": "Фандинг (funding rate) — плата между лонгами и шортами каждые 8 ч. "
               "Очень высокий положительный = перегрев лонгов, риск сквиза вниз.",
    "oi": "Open Interest — общий объём открытых контрактов. Рост OI + рост цены = "
          "входят новые деньги; падение OI + рост цены = короткое покрытие.",
    "rr": "R:R (risk/reward) — отношение потенциальной прибыли к риску. 1:2 означает "
          "риск 1, потенциал 2. HYPE не публикует сетапы с R:R ниже порога.",
    "score": "Оценка сетапа (0..100) — насколько факторы рынка совпали: тренд, структура, "
             "объёмы, стакан, деривативы. Это НЕ вероятность прибыли: сетап 90/100 тоже "
             "может закрыться по стопу. " + QUALITY_LEGEND.replace("\n", " · "),
    "tp": "TP (тейк-профит) — цели фиксации прибыли. SL (стоп-лосс) — цена, при достижении "
          "которой выходим: идея отменена. Стоп НЕ двигать дальше от цены.",
    "regime": "Режим рынка — глобальная обстановка (тренд/диапазон/высокая волатильность). "
              "От него зависят критерии сигналов: в сильном ап-тренде шорты не ищут «на RSI».",
    "vwap": "VWAP — средневзвешенная цена по объёму. Часто служит уровнем "
            "возврата/поддержки при коррекции.",
    "liquidity": "Ликвидность — глубина стакана и спред. Чем тоньше ликвидность, тем "
                 "выше риск проскальзывания; HYPE применяет повышенные требования к малым монетам.",
    "squeeze": "Squeeze — сжатие волатильности (Bollinger внутри Keltner). Часто "
               "предшествует резкому движению, но направление не гарантируется.",
    "emergence": "«Намечается движение» (подогрев 0–100) — HYPE ищет монеты ДО начала "
                 "движения: объём проснулся (RVOL), волатильность сжалась и начинает "
                 "расширяться, монета в узком диапазоне (накопление) или у границы "
                 "24h-диапазона на растущем объёме. Это признак раннего отбора, а НЕ "
                 "гарантия и НЕ приказ входить: направление всегда подтверждает "
                 "основной движок с детерминированным гейтом.",
    "rvol": "RVOL (относительный объём) — объём последнего часа по сравнению со средним "
            "объёмом этой монеты. RVOL 2 = объём вдвое выше обычного: кто-то активно "
            "заходит. Один из главных признаков «движение только начинается».",
    "positioning": "Positioning («кто и где стоит») — сочетание OI (открытые контракты), "
                   "фандинга и цены: входят ли новые деньги (OI растёт), не перегреты ли "
                   "лонги/шорты, нет ли капитуляции. Например: OI растёт + цена падает + "
                   "высокий фандинг = перегрев лонгов, риск резкой коррекции.",
    "cvd": "CVD (delta) — оценка агрессивных покупок/продаж по объёму баров. "
           "Используется как подтверждение, не как самостоятельный сигнал.",
    "entry": "Entry zone — зона входа (диапазон цен). HYPE якорит её на структуру "
             "(поддержка/VWAP) и волатильность, а не на одну точку.",
    "invalidation": "Инвалидация — условие, при котором идея становится недействительной "
                    "(обычно закрытие свечи за стопом). Это главный риск-контроль.",
    "confidence": "Confidence — полнота данных (0..1), а не вероятность прибыли. "
                  "Signal Quality (0..100) — качество сетапа. Это разные вещи.",
    "list": "Глоссарий — объяснение терминов простым языком. Выберите термин выше.",
}


def render_glossary(term: str) -> str:
    if term == "list":
        return "📚 **ГЛОССАРИЙ**\n\n" + "\n".join(
            f"• `{k.upper()}` — {v.split('.')[0]}." for k, v in GLOSSARY.items() if k != "list"
        ) + "\n\nНажмите кнопку с термином, чтобы получить полное объяснение."
    body = GLOSSARY.get(term.lower())
    if not body:
        return "❓ Неизвестный термин. Откройте глоссарий кнопкой «📚 ПОМОЩЬ»."
    return f"❓ **{term.upper()}**\n\n{body}"


# ── оценка сетапа ───────────────────────────────────────────────
def quality_label(quality: float, tier: str = "", cfg: SignalConfig | None = None) -> str:
    """«72/100 (A — хороший)». Оценка = качество сетапа, НЕ вероятность прибыли."""
    cfg = cfg or SignalConfig()
    if quality >= cfg.S_TIER_MIN:
        label = "S — отличный"
    elif quality >= cfg.A_TIER_MIN:
        label = "A — хороший"
    elif quality >= cfg.B_TIER_MIN:
        label = "B — средний, нужна дисциплина"
    elif quality >= cfg.C_TIER_MIN:
        label = "C — слабый, обычно не входим"
    else:
        label = "ниже порога — не входим"
    return f"{quality:.0f}/100 ({label})"


def source_stamp(source: str = "", ts_ms: int = 0, data_age_seconds: float | None = None) -> str:
    """«📡 Bybit v5 · обновлено 17:31:02 UTC · возраст 12с» — всегда виден."""
    src = {"bybit": "Bybit v5", "binance": "Binance Futures", "mexc": "MEXC Futures"}.get(
        (source or "").lower(), (source or "реальная биржа").capitalize()
    )
    when = time.strftime("%H:%M:%S UTC", time.gmtime(ts_ms / 1000.0)) if ts_ms else "?"
    age = f" · возраст {data_age_seconds:.0f}с" if data_age_seconds is not None else ""
    return f"📡 {src}{' · обновлено ' + when if ts_ms else ''}{age}"


def plain_reasons(signal: TradingSignal) -> list[str]:
    """2–4 объяснения обычными словами — генерируются из признаков сигнала."""
    features = signal.features or {}
    views = features.get("timeframes", []) or []
    der = features.get("derivatives", {}) or {}
    of = features.get("orderflow", {}) or {}
    ctx = features.get("context", {}) or {}
    scenario = features.get("scenario") or signal.scenario
    out: list[str] = []
    direction = signal.direction
    want = "up" if direction == "LONG" else "down"

    if scenario == "liquidity_sweep":
        out.append("уровень пробили фитилём и цена вернулась — ложный пробой (вынос стопов)")
    elif scenario == "reversal_choch":
        out.append("структура сменила характер (CHoCH) — ранний разворотный сценарий")
    elif scenario == "range_reversion":
        out.append("цена у границы диапазона — игра на возврат к середине")
    elif scenario == "breakout_watch":
        out.append("волатильность сжата — ждём подтверждённый пробой")
    else:
        aligned = [str(v.get("timeframe")) for v in views[:4] if v.get("trend") == want]
        if aligned:
            verb = "растёт" if direction == "LONG" else "падает"
            out.append(f"цена {verb} на {', '.join(aligned[:3])}")
        entry = views[0] if views else {}
        if "BOS" in str(entry.get("structure_signal", "")):
            out.append("пробой структуры (BOS) подтверждён")

    # ⚡ «намечается движение» — простыми словами (раунд 4)
    em = features.get("emergence") or {}
    if em and signal.direction in ("LONG", "SHORT"):
        ignition = float(em.get("ignition", 0.0) or 0.0)
        if ignition >= SignalConfig().EMERGENCE_IGNITION_MIN:
            hint = {"LONG": "вверх", "SHORT": "вниз"}.get(str(em.get("early_direction")), "")
            lead = f"движение только намечается ({'возможно ' + hint if hint else 'направление пока неясно'})"
            notes = [n for n in em.get("notes", []) if n][:2]
            out.append(lead + (": " + "; ".join(notes) if notes else ""))

    # кто и где стоит (positioning) — простыми словами
    pos = der.get("positioning")
    if pos == "healthy_long":
        out.append("в монету заходят деньги: открытые позиции (OI) растут при спокойной цене — строят лонг")
    elif pos == "overheated_long":
        out.append("внимание: монета перегрета — лонги перегружены, риск резкой коррекции")
    elif pos == "short_build":
        out.append("сейчас ставят на падение: открытые позиции (OI) растут, а цена идёт вниз")
    elif pos == "capitulation":
        out.append("признак капитуляции: массовое закрытие лонгов — часто разворот")
    elif pos == "short_squeeze":
        out.append("шорты выкупают — резкий рост может быть избыточным")

    ft = der.get("funding_trend")
    if ft in ("neutral", "falling"):
        out.append("фандинг нейтральный — перегрева нет")
    elif (ft == "overheated_long" and direction == "LONG") or (ft == "overheated_short" and direction == "SHORT"):
        out.append("внимание: фандинг перегрет на стороне сделки")

    grade = of.get("liquidity_grade")
    if grade in ("excellent", "ok"):
        out.append("стакан плотный, ликвидность хорошая")
    elif grade == "thin":
        out.append("стакан тонковат — входим аккуратно")

    btc = ctx.get("btc_trend")
    if btc == "up" and direction == "LONG":
        out.append("BTC растёт — рынок поддерживает лонг")
    elif btc == "down" and direction == "SHORT":
        out.append("BTC падает — рынок поддерживает шорт")

    dedup: list[str] = []
    for r in out:
        if r and r not in dedup:
            dedup.append(r)
    return dedup[:4]


# ── setup lists ─────────────────────────────────────────────────
def _targets_pct_line(sig: TradingSignal) -> str | None:
    """«цели: 0.0887 (+2.9%) → 0.0910 (+5.6%) → 0.0935 (+8.5%)» от входа."""
    if not sig.targets or not sig.entry_zone or not sig.entry_zone[0]:
        return None
    entry = (sig.entry_zone[0] + sig.entry_zone[1]) / 2
    parts = []
    for t in sig.targets[:3]:
        if sig.direction == "LONG":
            pct = (t / entry - 1.0) * 100.0 if entry else 0.0
        else:
            pct = (1.0 - t / entry) * 100.0 if entry else 0.0
        parts.append(f"{t:.6g} ({pct:+.1f}%)")
    return " • ".join([f"цели: {' → '.join(parts)}"])


def render_setup_row(item: dict[str, Any], place: int, cfg: SignalConfig | None = None) -> str:
    sig: TradingSignal = item["signal"]
    cfg = cfg or SignalConfig()
    emoji = "🟢" if sig.direction == "LONG" else "🔻"
    em = (sig.features or {}).get("emergence") or {}
    ignite = float(em.get("ignition", 0.0) or 0.0)
    marker = " ⚡" if ignite >= cfg.EMERGENCE_IGNITION_MIN else ""
    lines = [
        f"{place}. {emoji} **{sig.symbol}** — {sig.direction}{marker}",
        f"   Оценка сетапа: {quality_label(sig.quality, sig.tier, cfg)}",
        f"   • вход {sig.entry_zone[0]:.6g}–{sig.entry_zone[1]:.6g} · стоп {sig.stop_loss:.6g}",
    ]
    if ignite >= cfg.EMERGENCE_IGNITION_MIN:
        hint = {"LONG": "вверх", "SHORT": "вниз"}.get(str(em.get("early_direction")), "")
        lines.append("   ⚡ движение только намечается" + (f" (вероятно, {hint})" if hint else ""))
    targets = _targets_pct_line(sig)
    if targets:
        lines.append(f"   • {targets}")
    rb = sig.risk_brief if sig.risk_brief is not None else None
    leverage = sig.leverage or (rb.leverage if rb else 1)
    risk_pct = rb.max_deposit_pct if rb and rb.max_deposit_pct else 0.0
    lines.append(f"   • плечо до {leverage}x · риск ~{risk_pct:.1f}% депозита")
    why = plain_reasons(sig)
    if why:
        lines.append(f"   Почему: {' · '.join(why)}")
    if sig.condition:
        lines.append(f"   ⚠️ Условный сетап: {sig.condition}")
    return "\n".join(lines)


def render_setup_list(
    items: list[dict[str, Any]],
    title: str,
    page: int,
    pages: int,
    cfg: SignalConfig | None = None,
    stats_line: str = "",
    empty_hint: str = "",
) -> str:
    if not items:
        lines = [
            title,
            "",
            "😶 Сейчас нет подходящих сетапов.",
        ]
        if empty_hint:
            lines += ["", empty_hint]
        lines += [
            "",
            "Система честно говорит NO TRADE вместо того, чтобы выдумывать сигнал.",
            "❗ Это аналитика, не гарантия результата.",
        ]
        return "\n".join(lines)
    start = page * 8
    chunk = items[start : start + 8]
    lines = [title]
    if stats_line:
        lines.append(stats_line)
    lines += ["", f"Страница {page + 1}/{pages}. Отсортировано по качеству:", ""]
    for i, item in enumerate(chunk, start + 1):
        lines.append(render_setup_row(item, i, cfg))
        lines.append("")
    lines.extend([
        "❗ Оценка — качество сетапа, а не вероятность прибыли. Это аналитика, не гарантия результата.",
    ])
    return "\n".join(lines)


# ── scan summary ────────────────────────────────────────────────
_REJECT_LABELS: list[tuple[str, str]] = [
    ("r:r", "R:R ниже порога"),
    ("risk score", "риск выше лимита"),
    ("quality", "качество ниже порога"),
    ("spread", "широкий спред"),
    ("timeframe conflict", "конфликт таймфреймов"),
    ("order-book liquidity", "тонкий стакан"),
    ("turnover", "слабый оборот"),
    ("stale", "устаревшие данные"),
    ("no directional setup", "нет направления в текущем режиме"),
    ("no usable timeframe", "нет данных свечей"),
    ("no real market data", "нет реальных данных"),
]


def scan_summary(
    scanned_total: int,
    candidates: int,
    analyzed: int,
    setups: list[dict[str, Any]],
    mode: str,
    duration_sec: float = 0.0,
    ts_ms: int = 0,
) -> str:
    """«Сканировано 250 · кандидатов 47 · сетапов 6 (A:1 B:3 C:2) · Bybit · 17:31 UTC»."""
    tiers: dict[str, int] = {}
    for item in setups:
        t = item["signal"].tier
        tiers[t] = tiers.get(t, 0) + 1
    tier_bits = " ".join(f"{t}:{tiers[t]}" for t in ("S", "A", "B", "C") if tiers.get(t)) or "—"
    when = time.strftime("%H:%M UTC", time.gmtime(ts_ms / 1000.0)) if ts_ms else time.strftime("%H:%M UTC", time.gmtime())
    src = {"bybit": "Bybit", "binance": "Binance", "mexc": "MEXC"}.get((mode or "").lower(), mode or "?")
    dur = f" · {duration_sec:.1f}с" if duration_sec else ""
    return (
        f"Сканировано {scanned_total} · кандидатов {candidates} · сетапов {len(setups)} ({tier_bits})"
        f" · источник: {src} · {when}{dur}"
    )


def empty_list_hint(analyzed: list[dict[str, Any]], candidates_count: int = 0) -> str:
    """Почему список пуст: топ причин гейта по техническим (pro) причинам."""
    counts: dict[str, int] = {}
    passed_base = 0
    for item in analyzed:
        sig = item["signal"]
        if sig.direction in ("LONG", "SHORT"):
            passed_base += 1  # прошли движок, но ниже list-порога качества
            continue
        reasons = sig.no_trade_reasons or ["no directional setup"]
        seen: set[str] = set()
        for reason in reasons[:3]:
            low = reason.lower()
            for needle, label in _REJECT_LABELS:
                if needle in low and label not in seen:
                    counts[label] = counts.get(label, 0) + 1
                    seen.add(label)
                    break
    total = len(analyzed)
    if passed_base and total and passed_base == total:
        return f"все {total} кандидатов прошли движок, но слабее порога показа — рынок не даёт сильных сетапов"
    bits = ", ".join(f"{label} у {n}" for label, n in sorted(counts.items(), key=lambda x: -x[1])[:4])
    if not bits:
        return "последний скан не нашёл даже базовых кандидатов: рынок вне критериев ликвидности"
    prefix = f"все {total} кандидатов не прошли гейт: " if total else ""
    return prefix + bits


# ── market overview ─────────────────────────────────────────────
def render_market(overview: dict[str, Any], source: str = "") -> str:
    ts = overview.get("ts_ms", 0) / 1000
    when = time.strftime("%H:%M:%S UTC", time.gmtime(ts)) if ts else "?"
    btc = overview.get("btc") or {}
    eth = overview.get("eth") or {}
    g = overview.get("global") or {}
    fg = g.get("fear_greed") or {}
    trend_emoji = {"up": "🟢", "down": "🔴", "flat": "🟡"}.get(str(overview.get("btc_trend", "flat")), "🟡")
    mode = source or overview.get("mode", "?")

    trend_word = {"up": "растёт", "down": "падает", "flat": "боковик"}.get(str(overview.get("btc_trend", "flat")), "неясно")
    lines = [
        "📊 **МОЙ РЫНОК**",
        f"{source_stamp(mode, overview.get('ts_ms', 0))} · {when}",
        "",
        f"₿ **BTC** {btc.get('price', 0):.6g} | за 24ч {btc.get('price_24h_pct', 0):+.2f}%",
        f"   {trend_emoji} тренд (1ч): {trend_word} | волатильность ≈ {overview.get('btc_atr_pct') or 0:.2f}% за час",
        f"   фандинг {(overview.get('btc_funding_rate') or 0) * 1:.4%} | доля BTC {g.get('btc_dominance') or 0:.1f}%",
        f"Ξ **ETH** {eth.get('price', 0):.6g} | за 24ч {overview.get('eth_24h_pct') or eth.get('price_24h_pct', 0):+.2f}% | фандинг {(overview.get('eth_funding_rate') or 0) * 1:.4%}",
        "",
        f"🌐 Рынок: {g.get('market_cap_change_24h_pct') or 0:+.2f}% за 24ч",
        f"😨 Fear & Greed: {fg.get('value', '?')}/100 ({fg.get('classification', '?')})",
        f"📈 Инструментов: {overview.get('universe_count', 0)} | оборот 24ч ${overview.get('total_turnover_24h', 0) / 1e9:.1f}B",
        f"📉 Средний move: {overview.get('avg_move_24h_pct', 0):+.2f}%",
        "",
        "🔥 **ТОП ПО ОБОРОТУ**",
    ]
    for t in (overview.get("top_turnover") or [])[:5]:
        lines.append(f"  {t['symbol']} {t['price']:.6g} {t['price_24h_pct']:+.2f}%")
    lines += ["", "🚀 **ГАЙНЕРЫ 24H**"]
    for t in (overview.get("gainers") or [])[:5]:
        lines.append(f"  {t['symbol']} {t['price_24h_pct']:+.2f}% (${t['turnover_24h']/1e6:.0f}M)")
    lines += ["", "💀 **ЛУЗЕРЫ 24H**"]
    for t in (overview.get("losers") or [])[:5]:
        lines.append(f"  {t['symbol']} {t['price_24h_pct']:+.2f}% (${t['turnover_24h']/1e6:.0f}M)")
    lines += ["", "❗ Обзор, не инвестиционная рекомендация."]
    return "\n".join(lines)


# ── нет реальных данных ─────────────────────────────────────────
def render_no_data(reasons: list[str], diagnostics: list[dict[str, Any]] | None = None) -> str:
    """Анализ не запускается без минимального набора реальных данных."""
    lines = [
        "⚠️ **НЕТ РЕАЛЬНЫХ ДАННЫХ — анализ невозможен**",
        "",
        "Платформа работает только на реальных данных бирж и не подставляет "
        "синтетику. Сейчас минимальный набор (тикер + свечи) недоступен.",
        "",
        "Причины:",
    ]
    for r in reasons[:5]:
        lines.append(f"  • {r}")
    if diagnostics:
        lines += ["", "Источники:"]
        for row in diagnostics[:6]:
            state = "✅ доступен" if (row.get("available") or row.get("healthy")) else "❌ недоступен"
            err = f" — {row.get('last_error')}" if row.get("last_error") else ""
            lines.append(f"  • {row.get('source', '?')}: {state}{err}")
    lines += [
        "",
        "Нажмите «🔄 ПОПРОБОВАТЬ СНОВА» — система перепроверит реальные источники.",
    ]
    return "\n".join(lines)


def render_settings(settings: dict[str, Any]) -> str:
    return (
        "⚙️ **НАСТРОЙКИ АНАЛИЗА**\n\n"
        f"🧠 Режим отчёта: **{'PRO' if settings.get('mode') == 'pro' else 'BEGINNER'}**\n"
        f"💰 Депозит: **${settings.get('deposit_usd', 0):,.0f}** — используется для расчёта позиции\n"
        f"⚠️ Риск на сделку: **{settings.get('risk_per_trade_pct', 1):g}%**\n\n"
        "Настройки сохраняются локально для вашего Telegram-аккаунта.\n"
        "❗ Депозит и риск — это параметры расчёта, а не приказ торговать."
    )


ACCESS_DENIED = (
    "⛔ **НЕТ ДОСТУПА**\n\n"
    "Бот закрыт. Ваш Telegram ID не входит в список разрешённых пользователей.\n"
    "Администратор: задайте `TELEGRAM_ALLOWED_USER_IDS` (через запятую) в переменных окружения."
)
