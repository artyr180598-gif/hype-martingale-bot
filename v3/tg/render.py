"""Telegram renderers: compact rows, market overview, settings, glossary.

The full analysis card lives in ``v3/report.py``; this module renders *lists*
and *explainers* used by the interactive platform UI.
"""

from __future__ import annotations

import time
from typing import Any

from v3.models import TradingSignal

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
          "риск 1, потенциал 2. HYPE не публикует сетапы с R:R ниже MIN_RISK_REWARD.",
    "regime": "Режим рынка — глобальная обстановка (тренд/диапазон/высокая волатильность). "
              "От него зависят критерии сигналов: в сильном ап-тренде шорты не ищут «на RSI».",
    "vwap": "VWAP — средневзвешенная цена по объёму. Часто служит уровнем "
            "возврата/поддержки при коррекции.",
    "liquidity": "Ликвидность — глубина стакана и спред. Чем тоньше ликвидность, тем "
                 "выше риск проскальзывания; HYPE применяет повышенные требования к малым монетам.",
    "squeeze": "Squeeze — сжатие волатильности (Bollinger внутри Keltner). Часто "
               "предшествует резкому движению, но направление не гарантируется.",
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


# ── setup lists ─────────────────────────────────────────────────
def render_setup_row(item: dict[str, Any], place: int) -> str:
    sig: TradingSignal = item["signal"]
    cand = item.get("candidate", {})
    emoji = "🟢" if sig.direction == "LONG" else "🔻"
    return (
        f"{place}. {emoji} **{sig.symbol}** — {sig.direction} | "
        f"Quality {sig.quality:.0f}/100 ({sig.tier})\n"
        f"   Вход {sig.entry_zone[0]:.6g}–{sig.entry_zone[1]:.6g} · SL {sig.stop_loss:.6g} "
        f"· R:R 1:{sig.rr:.1f} · {sig.regime}\n"
        f"   _heat {cand.get('heat', 0):.1f}, {sig.features.get('regime', {}).get('note', '')[:80]}_"
    )


def render_setup_list(items: list[dict[str, Any]], title: str, page: int, pages: int) -> str:
    if not items:
        return (f"{title}\n\n"
                "😶 Сейчас нет подходящих сетапов. "
                "Сначала запустите «🔎 СКАНИРОВАТЬ РЫНОК» — и/или рынок пока не даёт чистой структуры.\n\n"
                "Система честно говорит NO TRADE вместо того, чтобы выдумывать сигнал.")
    start = page * 8
    chunk = items[start : start + 8]
    lines = [title, "", f"Страница {page + 1}/{pages}. Отсортировано по качеству:", ""]
    for i, item in enumerate(chunk, start + 1):
        lines.append(render_setup_row(item, i))
    lines.extend([
        "",
        "Раздел: ⭐ ТОП ВОЗМОЖНОСТИ",
        "❗ Это аналитика, не гарантия результата.",
    ])
    return "\n".join(lines)


# ── market overview ─────────────────────────────────────────────
def render_market(overview: dict[str, Any]) -> str:
    ts = overview.get("ts_ms", 0) / 1000
    when = time.strftime("%H:%M:%S UTC", time.gmtime(ts)) if ts else "?"
    btc = overview.get("btc") or {}
    eth = overview.get("eth") or {}
    g = overview.get("global") or {}
    fg = g.get("fear_greed") or {}
    trend_emoji = {"up": "🟢", "down": "🔴", "flat": "🟡"}.get(str(overview.get("btc_trend", "flat")), "🟡")

    lines = [
        "📊 **МОЙ РЫНОК**",
        f"🕐 {when} · режим: {overview.get('mode', '?')}" + (" · demo" if overview.get("is_demo") else ""),
        "",
        f"₿ **BTC** {btc.get('price', 0):.6g} | 24h {btc.get('price_24h_pct', 0):+.2f}%",
        f"   {trend_emoji} тренд (1h): {overview.get('btc_trend', '?')} | ATR {overview.get('btc_atr_pct') or 0:.2f}%",
        f"   funding {overview.get('btc_funding_rate') or 0 * 1:.4%} | доминация {g.get('btc_dominance') or 0:.1f}%",
        f"Ξ **ETH** {eth.get('price', 0):.6g} | 24h {overview.get('eth_24h_pct') or eth.get('price_24h_pct', 0):+.2f}% | funding {overview.get('eth_funding_rate') or 0 * 1:.4%}",
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
