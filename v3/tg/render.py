"""Telegram renderers: compact rows, market overview, settings, glossary.

The full analysis card lives in ``v3/report.py``; this module renders *lists*
and *explainers* used by the interactive platform UI.

Beginner-facing rules (жёстко):
  * никаких внутренних переменных движка (heat/adx/vol_z/trend_score/...) —
    только слова и понятные числа;
  * три разные метрики называются по-разному и объясняются рядом с цифрой:
    «Оценка сетапа» (качество сетапа), «Уверенность бота» (согласованность
    анализов, %) и «Полнота данных» (сколько источников ответило). Ни одна из
    них не выдаётся за вероятность прибыли;
  * у каждого вывода — реальный источник данных и timestamp.
"""

from __future__ import annotations

import time
from typing import Any

from v3.analysis.confidence import ConfidenceReport, assess_confidence
from v3.config import SignalConfig, build_line
from v3.models import TradingSignal

# Сколько сетапов на странице списка. Совпадает с ``v3.tg.keyboards.PAGE_SIZE``
# (проверяется тестом): пагинация и рендер обязаны считать страницы одинаково.
LIST_PAGE_SIZE = 8

QUALITY_LEGEND = (
    "Шкала оценки сетапов:\n"
    "  S 82–100 — отличный\n"
    "  A 72–81 — хороший\n"
    "  B 62–71 — средний, нужна дисциплина\n"
    "  C 50–61 — слабый, обычно не входим\n"
    "  ниже 55 — жёсткий минимум: вход запрещён (NO TRADE)\n"
    "Оценка — это качество сетапа, а не вероятность прибыли."
)

# Режим рынка → человеческие слова (новичку «TRENDING_UP» ничего не говорит).
REGIME_WORDS = {
    "TRENDING_UP": "восходящий тренд",
    "TRENDING_DOWN": "нисходящий тренд",
    "RANGING": "боковик (флэт)",
    "HIGH_VOLATILITY": "высокая волатильность",
    "LOW_VOLATILITY": "низкая волатильность",
    "BREAKOUT": "пробой вверх",
    "BREAKDOWN": "пробой вниз",
    "ACCUMULATION": "накопление",
    "DISTRIBUTION": "распределение",
    "UNCERTAIN": "неопределённый",
}


def regime_words(regime: str) -> str:
    """«TRENDING_UP» → «восходящий тренд». Неизвестный режим показываем как есть."""
    return REGIME_WORDS.get(str(regime or "").upper(), str(regime or "не определён"))

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
    "bot_confidence": "Уверенность бота (0–100%) — сводная цифра: насколько НЕЗАВИСИМЫЕ "
                      "анализы бота согласны между собой. Считается из шести блоков: "
                      "качество сетапа (34%), свежесть и полнота данных (16%), "
                      "согласованность таймфреймов (16%), объём/стакан/позиции (14%), "
                      "риск-профиль (10%), ранняя готовность импульса (10%). В карточке "
                      "сигнала всегда видно, из чего сложилась цифра и что её снижает. "
                      "Это НЕ вероятность прибыли: 85% уверенности не означают 85% шансов "
                      "на прибыль — сделка всё равно может закрыться по стопу.",
    "data_completeness": "Полнота данных (%) — сколько реальных источников биржи ответило "
                         "и насколько свежими были свечи. Полнота 100% = тикер, свечи всех "
                         "таймфреймов, стакан, фандинг, OI и ликвидации получены без "
                         "задержек. Низкая полнота режет уверенность бота: анализ на "
                         "неполных данных хуже, и бот это показывает честно.",
    "auto_alert": "Авто-сигнал — уведомление БЕЗ вашего запроса. Бот сам сканирует рынок "
                  "каждые несколько минут и пишет вам только тогда, когда сетап проходит "
                  "все пороги качества (оценка сетапа, уверенность бота, полнота данных, "
                  "риск, потенциал к риску). Всё, что не дотянуло, остаётся в разделах "
                  "«⭐ ТОП / 🔥 LONG / 🔻 SHORT» — бот молчит, а не спамит.",
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
    "impulse_phase": "Фаза импульса: EARLY — база только просыпается; TRIGGERED — закрытая "
                     "свеча уже подтвердила выход, но движение ещё не убежало; EXHAUSTED — "
                     "движение слишком далеко, такой вход отбрасывается. Это фильтр времени, "
                     "а не прогноз цены.",
    "cvd": "CVD (delta) — оценка агрессивных покупок/продаж по объёму баров. "
           "Используется как подтверждение, не как самостоятельный сигнал.",
    "entry": "Entry zone — зона входа (диапазон цен). HYPE якорит её на структуру "
             "(поддержка/VWAP) и волатильность, а не на одну точку.",
    "invalidation": "Инвалидация — условие, при котором идея становится недействительной "
                    "(обычно закрытие свечи за стопом). Это главный риск-контроль.",
    "confidence": "Три разные метрики — не путайте: «Оценка сетапа» (0..100) — качество "
                  "сетапа; «Уверенность бота» (0–100%) — согласованность независимых "
                  "анализов; «Полнота данных» (%) — сколько реальных источников ответило. "
                  "Ни одна из них не является вероятностью прибыли.",
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


def version_line(cfg: SignalConfig | None = None) -> str:
    """«🛠 Сборка: v3.2.0 · Раунд 5: …» — одна и та же строка во всех интерфейсах.

    Зачем: без неё пользователь не может отличить свежий процесс от старого
    (код обновлён, а запущенный бот — прежний).
    """
    cfg = cfg or SignalConfig()
    return build_line(cfg.APP_VERSION, cfg.APP_RELEASE)


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


# ── уверенность бота ────────────────────────────────────────────
def confidence_bar(percent: float, cells: int = 10) -> str:
    """«████████░░» — процент уверенности, который видно одним взглядом."""
    filled = int(round(max(0.0, min(100.0, float(percent))) / 100.0 * cells))
    return "█" * filled + "░" * (cells - filled)


def confidence_headline(report: ConfidenceReport) -> str:
    """«🎯 УВЕРЕННОСТЬ БОТА: 78% — высокая»."""
    return f"🎯 **УВЕРЕННОСТЬ БОТА: {report.percent:.0f}% — {report.label}**"


def confidence_block(report: ConfidenceReport | None = None, signal: Any = None, cfg: SignalConfig | None = None) -> list[str]:
    """Отдельный блок «Уверенность бота» с разбором по анализам.

    Возвращает строки (пустой список, если считать не из чего), чтобы карточка
    могла вставить блок целиком. Разбор обязателен: цифра без объяснения
    воспринимается как «шанс прибыли», а это неправда.
    """
    cfg = cfg or SignalConfig()
    if report is None:
        if signal is None:
            return []
        report = assess_confidence(signal, cfg)
    lines = [
        confidence_headline(report),
        f"{confidence_bar(report.percent)} {report.percent:.0f} из 100",
        "",
        "🔍 **Из чего сложилась уверенность** (вес каждого анализа):",
    ]
    for part in report.parts:
        weight = f"{part.weight * 100:.0f}%"
        note = f" — {part.note}" if part.note else ""
        lines.append(f"• {part.title}: **{part.score:.0f}%** (вес {weight}){note}")
    lines += [
        "",
        f"💡 Как читать: {report.verdict}.",
        f"⚠️ Чего цифра не обещает: {report.percent:.0f}% — это НЕ вероятность прибыли. "
        "Сетап с высокой уверенностью тоже может закрыться по стопу.",
    ]
    if report.warnings:
        lines.append("📉 Что снижает уверенность:")
        lines += [f"• {w}" for w in report.warnings]
    return lines


def confidence_line(report: ConfidenceReport | None = None, signal: Any = None, cfg: SignalConfig | None = None) -> str:
    """Одна строка для списков: «уверенность бота 78% (высокая)»."""
    cfg = cfg or SignalConfig()
    if report is None:
        if signal is None:
            return ""
        report = assess_confidence(signal, cfg)
    weak = report.weakest[0].title if report.weakest else ""
    tail = f" · слабое место: {weak}" if weak else ""
    return f"Уверенность бота: {report.percent:.0f}% ({report.label}){tail}"


def data_completeness_line(signal: TradingSignal) -> str:
    """«📦 Полнота данных: 90%» — честно, сколько источников ответило."""
    pct = max(0.0, min(100.0, float(signal.confidence or 0.0) * 100.0))
    if signal.stale:
        state = "данные устарели — сигнал неактуален"
    elif pct >= 95.0:
        state = "все источники ответили"
    elif pct >= 70.0:
        state = "часть источников неполная"
    else:
        state = "многих данных нет — анализ слабее"
    return f"📦 Полнота данных: {pct:.0f}% — {state}"


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
            early = str(em.get("early_direction"))
            hint = {"LONG": "вверх", "SHORT": "вниз"}.get(early, "")
            notes = [n for n in em.get("notes", []) if n][:2]
            if early in ("LONG", "SHORT") and early != signal.direction:
                # Противоречие не прячем: ранний импульс против сделки — это
                # риск, а не «подтверждение».
                out.append(
                    "осторожно: ранний отбор смотрит в другую сторону — сетап идёт против раннего импульса"
                )
            else:
                lead = f"движение только намечается ({'возможно ' + hint if hint else 'направление пока неясно'})"
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
    phase = str(em.get("phase", "NEUTRAL"))
    marker = " ⚡" if ignite >= cfg.EMERGENCE_IGNITION_MIN and phase != "EXHAUSTED" else ""
    lines = [
        f"{place}. {emoji} **{sig.symbol}** — {sig.direction}{marker}",
        f"   Оценка сетапа: {quality_label(sig.quality, sig.tier, cfg)}",
        f"   {confidence_line(signal=sig, cfg=cfg)}",
        f"   • вход {sig.entry_zone[0]:.6g}–{sig.entry_zone[1]:.6g} · стоп {sig.stop_loss:.6g}",
    ]
    if ignite >= cfg.EMERGENCE_IGNITION_MIN and phase != "EXHAUSTED":
        early = str(em.get("early_direction"))
        hint = {"LONG": "вверх", "SHORT": "вниз"}.get(early, "")
        phase_text = {
            "EARLY": "база просыпается",
            "TRIGGERED": "первый импульс подтверждён",
        }.get(phase, "движение только намечается")
        if early in ("LONG", "SHORT") and early != sig.direction:
            # Ранний отбор смотрит в другую сторону — честно предупреждаем,
            # а не показываем «вероятно вверх» рядом со сделкой на падение.
            lines.append(f"   ⚡ {phase_text}, но ранний отбор смотрит в другую сторону — сетап против импульса")
        else:
            lines.append(f"   ⚡ {phase_text}" + (f" (вероятно, {hint})" if hint else ""))
    targets = _targets_pct_line(sig)
    if targets:
        lines.append(f"   • {targets}")
    rb = sig.risk_brief if sig.risk_brief is not None else None
    leverage = sig.leverage or (rb.leverage if rb else 1)
    risk_pct = rb.max_deposit_pct if rb and rb.max_deposit_pct else 0.0
    lines.append(f"   • плечо до {leverage}x · риск ~{risk_pct:.1f}% депозита")
    why = plain_reasons(sig)[:3]
    if why:
        lines.append("   Почему:")
        lines += [f"   • {r}" for r in why]
    if sig.condition:
        lines.append(f"   ⚠️ Условный сетап: {sig.condition}")
    return "\n".join(lines)


# ── «⚡ НАМЕЧАЕТСЯ ДВИЖЕНИЕ» (ранний отбор) ───────────────────────
EMERGING_TITLE = "⚡ НАМЕЧАЕТСЯ ДВИЖЕНИЕ (ранний отбор)"
EMERGING_INTRO = (
    "Монеты, где движение только зарождается: объём проснулся, волатильность "
    "сжалась и начинает расширяться, цена у границы диапазона."
)
EMERGING_DISCLAIMER = (
    "⚠️ Это наблюдение, а НЕ команда входа: направление всегда подтверждает "
    "основной анализ, а гарантии движения нет. «Подогрев» — сила ранних "
    "признаков (0–100), не вероятность прибыли."
)
# Анти-chase заметки emergence объясняют, почему движение УЖЕ состоялось.
# В блок «намечается» они не идут: там только ранние признаки.
_EMERGENCE_NOT_EARLY = (
    "уже у вершины", "уже у вершины/дна", "близко к вершине", "уже у дна", "близко к дну",
)


_EARLY_WORDS = {
    "LONG": ("LONG (в лонг, ставка на рост)", "🟢"),
    "SHORT": ("SHORT (в шорт, ставка на падение)", "🔻"),
}
# Ранняя фаза = меньше подтверждений, значит и плечо меньше «боевого» лимита.
EMERGING_MAX_LEVERAGE = 3


def _emerging_direction(cand: dict[str, Any], sig: TradingSignal | None) -> str:
    """Куда, вероятно, пойдёт движение: сначала полный анализ, потом подсказка."""
    if sig is not None and getattr(sig, "direction", "") in ("LONG", "SHORT"):
        return str(sig.direction)
    early = str(cand.get("early_direction") or "FLAT")
    return early if early in ("LONG", "SHORT") else "FLAT"


def _emerging_levels(
    cand: dict[str, Any], sig: TradingSignal | None, direction: str
) -> list[str]:
    """Строки с ценами: вход, стоп, цели. Из сигнала — точные, иначе ориентиры."""
    if sig is not None and getattr(sig, "entry_zone", None) and sig.entry_zone[0]:
        out = [
            f"  Цены: вход {sig.entry_zone[0]:.6g}–{sig.entry_zone[1]:.6g}"
            f" · стоп {sig.stop_loss:.6g}"
        ]
        targets = _targets_pct_line(sig)
        if targets:
            out.append(f"  {targets[0].upper() + targets[1:]}")
        return out

    price = float(cand.get("price") or 0.0)
    if price <= 0 or direction not in ("LONG", "SHORT"):
        return []
    hi = float(cand.get("high_24h") or 0.0) or price * 1.05
    lo = float(cand.get("low_24h") or 0.0) or price * 0.95
    if direction == "LONG":
        entry_lo, entry_hi = price * 0.997, price * 1.004
        stop = max(lo, price * 0.95) * 0.999
        t1, t2 = price * 1.02, max(hi, price * 1.045)
    else:
        entry_lo, entry_hi = price * 0.996, price * 1.003
        stop = min(hi, price * 1.05) * 1.001
        t1, t2 = price * 0.98, min(lo, price * 0.955)
    return [
        f"  Цены (ориентир, цена сейчас {price:.6g}): вход {entry_lo:.6g}–{entry_hi:.6g}"
        f" · стоп {stop:.6g}",
        f"  Цели: {t1:.6g} → {t2:.6g}",
    ]


def _emerging_leverage_line(sig: TradingSignal | None, cfg: SignalConfig) -> str:
    """Плечо: берём из риск-брифа, но в ранней фазе режем до безопасного потолка."""
    rb = getattr(sig, "risk_brief", None) if sig is not None else None
    base = int(getattr(sig, "leverage", 0) or (rb.leverage if rb else 0) or 0)
    cap = min(EMERGING_MAX_LEVERAGE, int(cfg.MAX_LEVERAGE))
    lev = min(base, cap) if base else cap
    tail = ""
    if rb is not None and rb.max_deposit_pct:
        tail = f" · риск ~{rb.max_deposit_pct:.1f}% депозита"
    return f"  Плечо: не выше {lev}x (ранняя фаза — вход маленьким объёмом){tail}"


def _emergence_notes(item: dict[str, Any]) -> list[str]:
    """Готовые человеческие заметки emergence (из сигнала или из кандидата)."""
    sig = item.get("signal")
    em = (getattr(sig, "features", None) or {}).get("emergence") or {}
    notes = [str(n).strip() for n in (em.get("notes") or []) if str(n).strip()]
    if not notes:
        raw = str((item.get("candidate") or {}).get("emergence_note") or "")
        notes = [part.strip() for part in raw.split("|") if part.strip()]
    return [n for n in notes if not n.lower().startswith(_EMERGENCE_NOT_EARLY)]


def render_emerging(
    items: list[dict[str, Any]],
    cfg: SignalConfig | None = None,
    *,
    limit: int = 5,
    pro: bool = False,
) -> str:
    """Блок «⚡ НАМЕЧАЕТСЯ ДВИЖЕНИЕ» для ответа скана (Telegram; CLI печатает
    свой операторский вариант с сырыми числами).

    Пустой список → пустая строка: блок не печатается «для галочки».
    Заметки берём уже готовыми человеческим языком (их пишет emergence);
    сырые ignition / early_direction показываем только в PRO-режиме.
    """
    if not items:
        return ""
    cfg = cfg or SignalConfig()
    lines = [EMERGING_TITLE, EMERGING_INTRO, ""]
    for item in items[:limit]:
        cand = item.get("candidate") or {}
        sig = item.get("signal")
        symbol = str(cand.get("symbol") or getattr(sig, "symbol", "") or "?")
        notes = _emergence_notes(item)
        ignition = float(cand.get("ignition", 0.0) or 0.0)
        phase = str(cand.get("phase", "EARLY"))
        phase_text = {
            "EARLY": "база просыпается",
            "TRIGGERED": "первый импульс подтверждён",
            "NEUTRAL": "наблюдение",
        }.get(phase, "ранняя фаза")
        hint = " · ".join(notes[:2]) if notes else "признаков хватает, но коротко их не описать"
        direction = _emerging_direction(cand, sig)
        dir_text, dir_emoji = _EARLY_WORDS.get(
            direction, ("направление ещё не определилось — входа нет, только наблюдение", "⏳")
        )
        lines.append(f"• {dir_emoji} **{symbol}** — подогрев {ignition:.0f}/100 · фаза: {phase_text}")
        lines.append(f"  Куда: {dir_text}")
        if direction in ("LONG", "SHORT"):
            lines += _emerging_levels(cand, sig, direction)
            lines.append(_emerging_leverage_line(sig, cfg))
        lines.append(f"  Признаки: {hint}")
        if sig is not None:
            # Глубокий анализ уже есть — показываем обе метрики отдельными
            # строками: «подогрев» (ранние признаки) и «уверенность бота»
            # (согласованность полного анализа). Это разные вещи.
            lines.append(
                f"  Оценка сетапа: {quality_label(float(sig.quality or 0.0), str(sig.tier or ''), cfg)}"
                f" · {confidence_line(signal=sig, cfg=cfg)}"
            )
        if pro:
            lines.append(
                f"  [phase {phase}, ignition {ignition:.0f}/100, "
                f"hint {cand.get('early_direction', 'FLAT')}]"
            )
    lines += ["", EMERGING_DISCLAIMER]
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
    start = page * LIST_PAGE_SIZE
    chunk = items[start : start + LIST_PAGE_SIZE]
    lines = [title]
    if stats_line:
        lines.append(stats_line)
    lines += ["", f"Страница {page + 1}/{pages}. Отсортировано по качеству сетапа:", ""]
    for i, item in enumerate(chunk, start + 1):
        lines.append(render_setup_row(item, i, cfg))
        lines.append("")
    lines.extend([
        "❗ «Оценка сетапа» — качество сетапа, «Уверенность бота» — согласованность "
        "анализов. Ни то, ни другое не является вероятностью прибыли.",
        "❗ Это аналитика, не гарантия результата.",
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


# ── авто-сигналы ────────────────────────────────────────────────
def utc_time(ts_ms: int) -> str:
    """«14:22:07 UTC» — единый формат времени во всех разделах UI."""
    return time.strftime("%H:%M:%S UTC", time.gmtime(ts_ms / 1000.0)) if ts_ms else "—"


def _ago(ts_ms: int) -> str:
    if not ts_ms:
        return ""
    seconds = max(0, int(time.time() - ts_ms / 1000.0))
    if seconds < 60:
        return f" ({seconds}с назад)"
    if seconds < 3600:
        return f" ({seconds // 60} мин назад)"
    return f" ({seconds // 3600} ч назад)"


def alert_thresholds_lines(cfg: SignalConfig | None = None) -> list[str]:
    """Пороги авто-сигнала человеческим языком — один источник правды для UI."""
    cfg = cfg or SignalConfig()
    lines = [
        f"• Оценка сетапа ≥ {cfg.ALERT_MIN_QUALITY:.0f}/100",
        f"• Уверенность бота ≥ {cfg.ALERT_MIN_BOT_CONFIDENCE:.0f}%",
        f"• Полнота данных ≥ {cfg.ALERT_MIN_DATA_CONFIDENCE * 100:.0f}%",
        f"• Риск ≤ {cfg.ALERT_MAX_RISK_SCORE}/10 · потенциал к риску ≥ 1:{cfg.ALERT_MIN_RR:.1f}",
    ]
    if cfg.ALERT_REQUIRE_FRESH:
        lines.append(f"• Данные свежие (не старше {cfg.MAX_DATA_AGE_SECONDS:.0f}с)")
    lines.append(
        f"• Не чаще 1 сигнала по монете в {cfg.COOLDOWN_SECONDS // 60} мин · "
        f"максимум {cfg.ALERT_MAX_PER_CYCLE} за цикл"
    )
    return lines


def render_alerts_page(
    cfg: SignalConfig | None = None,
    *,
    enabled: bool = True,
    interval_seconds: int = 0,
    last_cycle_ms: int = 0,
    sent_total: int = 0,
    found_total: int = 0,
    last_alert_ms: int = 0,
    last_alert_symbol: str = "",
    active_signals: int = 0,
    scope: str = "",
    transport_enabled: bool = True,
    last_suppressed: str = "",
) -> str:
    """Раздел «🔔 АВТО-СИГНАЛЫ»: что делает бот, пороги, текущее состояние."""
    cfg = cfg or SignalConfig()
    interval = interval_seconds or cfg.WATCHER_INTERVAL_SECONDS
    minutes = max(1, round(interval / 60.0))
    status = (
        "✅ **включены** — бот пишет сам, как только находит сильный сетап"
        if enabled
        else "⏸ **выключены** — бот молчит, но анализ по вашему запросу работает"
    )
    lines = [
        "🔔 **АВТО-СИГНАЛЫ** (без вашего запроса)",
        "",
        f"Статус: {status}",
        "",
        "**Что делает бот:**",
        f"• Каждые ~{minutes} мин сам сканирует рынок USDT-perp",
        "• Проверяет лучших кандидатов полным анализом: тренды 5m–1d, структура, "
        "объёмы, стакан, открытые позиции, фандинг, ликвидации, риск",
        "• Пишет вам только если сетап прошёл ВСЕ пороги ниже; остальное остаётся "
        "в разделах «⭐ ТОП / 🔥 LONG / 🔻 SHORT» — бот молчит, а не спамит",
        "",
        "**Пороги авто-сигнала:**",
        *alert_thresholds_lines(cfg),
        "",
        "**Состояние:**",
        f"• Последний цикл скана: {utc_time(last_cycle_ms)}{_ago(last_cycle_ms)}",
        f"• Найдено достойных сетапов: {found_total} · отправлено вам: {sent_total}"
        + (f" · последний: {last_alert_symbol} в {utc_time(last_alert_ms)}" if last_alert_symbol else ""),
        f"• Активных сигналов под наблюдением: {active_signals}",
    ]
    if last_suppressed:
        lines.append(f"• Последний отказ: {last_suppressed}")
    if scope:
        lines.append(f"• Область поиска: {scope}")
    if not transport_enabled:
        lines.append(
            "• ⚠️ Доставка не настроена: задайте TELEGRAM_BOT_TOKEN и "
            "TELEGRAM_ADMIN_CHAT_ID (или ALERT_CHAT_IDS) в переменных окружения"
        )
    lines += [
        "",
        "❗ Авто-сигнал — аналитика, не гарантия результата и не приказ входить. "
        "Решение и риск всегда на вас.",
    ]
    return "\n".join(lines)


def render_alerts_found(texts: list[str], cfg: SignalConfig | None = None, checked: int = 0) -> str:
    """Ответ на «🔎 Проверить сейчас»: что нашлось за этот проход."""
    cfg = cfg or SignalConfig()
    head = "🔎 **ПРОВЕРКА РЫНКА ПО ЗАПРОСУ**"
    if not texts:
        return "\n".join([
            head,
            "",
            f"Проверено сетапов: {checked}" if checked else "Цикл проверки завершён.",
            "😶 Сильного сетапа нет: ни один кандидат не прошёл пороги авто-сигнала.",
            "",
            "Пороги, которые нужно пройти:",
            *alert_thresholds_lines(cfg),
            "",
            "Это нормально: система предпочитает пропустить сделку, чем войти в слабый сетап.",
            "❗ Аналитика, не гарантия результата.",
        ])
    return "\n\n".join([head, *texts])


def render_settings(settings: dict[str, Any], cfg: SignalConfig | None = None) -> str:
    cfg = cfg or SignalConfig()
    early = "включён" if cfg.SCAN_EMERGENCE_ENABLED else "выключен"
    minutes = max(1, round((cfg.WATCHER_INTERVAL_SECONDS) / 60.0))
    return (
        "⚙️ **НАСТРОЙКИ АНАЛИЗА**\n\n"
        f"🧠 Режим отчёта: **{'PRO' if settings.get('mode') == 'pro' else 'BEGINNER'}**\n"
        f"💰 Депозит: **${settings.get('deposit_usd', 0):,.0f}** — используется для расчёта позиции\n"
        f"⚠️ Риск на сделку: **{settings.get('risk_per_trade_pct', 1):g}%**\n"
        f"⚡ Ранний отбор «намечающегося движения»: **{early}**\n"
        f"🔔 Авто-сигналы: каждые ~{minutes} мин, порог уверенности "
        f"**{cfg.ALERT_MIN_BOT_CONFIDENCE:.0f}%** (раздел «🔔 АВТО-СИГНАЛЫ»)\n\n"
        f"{version_line(cfg)}\n"
        "Настройки сохраняются локально для вашего Telegram-аккаунта.\n"
        "❗ Депозит и риск — это параметры расчёта, а не приказ торговать."
    )


ACCESS_DENIED = (
    "⛔ **НЕТ ДОСТУПА**\n\n"
    "Бот закрыт. Ваш Telegram ID не входит в список разрешённых пользователей.\n"
    "Администратор: задайте `TELEGRAM_ALLOWED_USER_IDS` (через запятую) в переменных окружения."
)
