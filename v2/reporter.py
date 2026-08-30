"""
Отчётность v2: Markdown с эмодзи.

Формат жёстко задан ТЗ:
  * первая строка — **Вердикт: Входить / Не входить / Наблюдать**;
  * разделы: Безопасность → Рыночные данные → Технический анализ →
    Социальный фон → Рекомендации;
  * каждое число сопровождается пояснением («ATR = 0.05, поэтому стоп на 2%
    ниже») — пользователь должен понимать, откуда взялась цифра;
  * любые данные-заглушки помечаются ⚠️ и подписью источника.

Рендер ничего не знает о сети: на вход — CoinReport/ScanResult, на выход —
строка. Тот же текст уходит в Telegram, в CLI и в HTTP-API.
"""

from __future__ import annotations

from v2.config import V2Config
from v2.models import CoinReport, ScanResult

VERDICT_EMOJI = {"ENTER": "✅", "WATCH": "👀", "AVOID": "⛔"}


# ═══════════════════════════════════════════════════════════════
#  ФОРМАТИРОВАНИЕ ЧИСЕЛ
# ═══════════════════════════════════════════════════════════════
def usd(value: float | None) -> str:
    if value is None:
        return "нет данных"
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 1_000_000_000:
        return f"{sign}${value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"{sign}${value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"{sign}${value / 1_000:.1f}K"
    if value >= 1:
        return f"{sign}${value:,.2f}"
    return f"{sign}${value:.6f}"


def price(value: float | None) -> str:
    if value is None or value == 0:
        return "нет данных"
    if value >= 1000:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:.4f}"
    if value >= 0.001:
        return f"{value:.6f}"
    return f"{value:.10f}"


def pct(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "нет данных"
    return f"{value:+.{digits}f}%" if digits else f"{value:+.0f}%"


def flag(value: bool | None) -> str:
    if value is None:
        return "❔ нет данных"
    return "⛔ да" if value else "✅ нет"


def _grade_ru(grade: str) -> str:
    return {
        "excellent": "отличная",
        "ok": "приемлемая",
        "thin": "тонкая",
        "empty": "недостаточная",
    }.get(grade, "неизвестно")


# ═══════════════════════════════════════════════════════════════
#  ОТЧЁТ ПО МОНЕТЕ
# ═══════════════════════════════════════════════════════════════
def render_report(report: CoinReport, config: V2Config | None = None) -> str:
    """Полный Markdown-отчёт по монете."""
    token = report.token
    lines: list[str] = []

    # ── первая строка: вердикт ───────────────────────────────────
    emoji = VERDICT_EMOJI[report.verdict]
    lines.append(f"**Вердикт: {report.verdict_ru}** {emoji}")
    lines.append("")
    lines.append(
        f"🔥 **{token.symbol}** — {token.name or 'без названия'} "
        f"(`{token.chain}` / {token.dex or 'DEX'})"
    )
    lines.append(
        f"📊 Цена {usd(token.price_usd)} | 24ч {pct(token.price_change_24h_pct)} | "
        f"Объём 24ч {usd(token.volume_24h_usd)} | LP {usd(token.liquidity_usd)}"
    )
    lines.append(
        f"⚖️ Риск **{report.risk_score}/10** | Оценка **{report.score:.0f}/100** | "
        f"Уверенность в данных **{report.confidence * 100:.0f}%**"
    )
    if report.summary:
        lines.append(f"_{report.summary}_")
    lines.append("")

    # ── 1. БЕЗОПАСНОСТЬ ──────────────────────────────────────────
    sec = report.security
    lines.append("## 🛡️ Безопасность")
    lines.append(f"**Оценка: {sec.score:.0f}/100** — {'заблокировано' if sec.blocked else 'проверено'}")
    lines.append("")
    for blocker in sec.blockers:
        lines.append(f"- ⛔ {blocker.lstrip('⛔ ').strip()}")
    for warning in sec.warnings:
        lines.append(f"- ⚠️ {warning}")
    for passed in sec.passed:
        lines.append(f"- {passed.lstrip('✅ ').strip()} ✅")
    if not (sec.blockers or sec.warnings or sec.passed):
        lines.append("- ❔ Проверки не выполнены — см. раздел «Качество данных»")
    lines.append("")

    # детали проверок с числами
    h, lp, c, d = sec.holders, sec.lp, sec.contract, sec.deployer
    lines.append("**Детали проверок:**")
    if h.top10_pct is not None:
        lines.append(
            f"- 👥 Холдеры: топ-10 = **{h.top10_pct:.1f}%**, топ-1 = {h.top1_pct:.1f}%, "
            f"держателей {h.holders_count or '?'} — порог блокировки 40%, поэтому "
            f"{'токен отклонён' if h.top10_pct > 40 else 'концентрация приемлема'}"
        )
    else:
        lines.append("- 👥 Холдеры: данные не получены")
    if lp.locked_pct is not None:
        lock = "навсегда" if lp.locked_forever else f"{lp.lock_days_left:.0f} дней" if lp.lock_days_left is not None else "срок неизвестен"
        lines.append(
            f"- 🔒 Ликвидность: заблокировано **{lp.locked_pct:.0f}%** на {lock}"
            f"{f' ({lp.locker})' if lp.locker else ''} — требуется ≥80% и ≥180 дней"
        )
    else:
        lines.append("- 🔒 Ликвидность: данные о блокировке не получены")
    lines.append(
        f"- 📜 Контракт: mint() {flag(c.is_mintable)} | blacklist() {flag(c.has_blacklist)} | "
        f"honeypot {flag(c.is_honeypot)} | прокси {flag(c.is_proxy)}"
    )
    if c.buy_tax_pct is not None or c.sell_tax_pct is not None:
        lines.append(
            f"- 💸 Налоги: покупка {c.buy_tax_pct if c.buy_tax_pct is not None else '?'}%, "
            f"продажа {c.sell_tax_pct if c.sell_tax_pct is not None else '?'}% — "
            f"вычитаются из прибыли автоматически"
        )
    if c.functions_found:
        lines.append(f"- 🧩 Функции контракта: `{', '.join(c.functions_found[:8])}`")
    if c.ai_notes:
        lines.append(f"- 🤖 AI-аудит ({c.ai_verdict or 'n/a'}): {c.ai_notes}")
    if d.address:
        lines.append(
            f"- 🧑‍💻 Деплоер: `{d.address[:12]}…`, возраст "
            f"{f'{d.age_days:.0f} дней' if d.age_days is not None else 'неизвестен'}, "
            f"контрактов {d.tokens_deployed if d.tokens_deployed is not None else '?'}, "
            f"транзакций {d.tx_count if d.tx_count is not None else '?'}"
            + (" — ⛔ слил весь стейк" if d.sold_out else "")
            + (" — ⛔ в чёрных списках" if d.flagged else "")
        )
    else:
        lines.append("- 🧑‍💻 Деплоер: не определён (нужен ETHERSCAN_API_KEY/BSCSCAN_API_KEY)")
    if token.liq_to_mcap is not None:
        lines.append(
            f"- ⚖️ LP/капа = **{token.liq_to_mcap * 100:.1f}%** "
            f"({usd(token.liquidity_usd)} к {usd(token.market_cap_effective)}) — "
            "чем выше доля, тем легче выйти из позиции"
        )
    lines.append("")

    # ── 2. РЫНОЧНЫЕ ДАННЫЕ ───────────────────────────────────────
    m = report.micro
    lines.append("## 📊 Рыночные данные (микроструктура)")
    lines.append(
        f"**Ликвидность {_grade_ru(m.grade)}** — проскальзывание входа на "
        f"{usd(m.entry_size_usd)}: **{m.slippage_pct:.2f}%**"
    )
    lines.append("")
    lines.append(
        f"- 💱 Спред {m.spread_pct:.3f}% — цена покупки выше mid на эту величину"
    )
    lines.append(
        f"- 🧱 Глубина ±1%: биды {usd(m.bid_depth_1pct_usd)} / аски {usd(m.ask_depth_1pct_usd)}; "
        f"перекос {m.imbalance:+.2f} (плюс — давление покупателей)"
    )
    if m.biggest_ask_wall_usd:
        lines.append(
            f"- 🧊 Стена продаж {usd(m.biggest_ask_wall_usd)} на {price(m.biggest_ask_wall_price)} — "
            "цене нужно «съесть» её, чтобы пойти выше"
        )
    if m.biggest_bid_wall_usd:
        lines.append(
            f"- 🧊 Стена покупок {usd(m.biggest_bid_wall_usd)} на {price(m.biggest_bid_wall_price)} — "
            "поддержка при откате"
        )
    if m.est_fill_price:
        lines.append(
            f"- 🎯 Расчётная цена исполнения {price(m.est_fill_price)} "
            f"(потери на входе ≈ {usd(m.slippage_cost_usd)})"
        )
    for note in m.notes:
        lines.append(f"- ℹ️ {note}")
    if m.is_stub:
        lines.append("- ⚠️ Стакан эмулирован из ликвидности пула (у DEX нет биржевого стакана)")
    lines.append("")

    # ── 3. ТЕХНИЧЕСКИЙ АНАЛИЗ ────────────────────────────────────
    t = report.technical
    tr, acc, fib = t.trend, t.accumulation, t.fib
    lines.append("## 📈 Технический анализ")
    lines.append(
        f"**Тренд на {tr.timeframe}: {tr.direction}** (ADX {tr.adx:.0f}, сила: {tr.strength})"
    )
    lines.append("")
    lines.append(
        f"- 📐 ADX {tr.adx:.0f}: DI+ {tr.plus_di:.0f} против DI− {tr.minus_di:.0f} — "
        + (
            "тренд выраженный, торгуем по направлению"
            if tr.adx >= 25
            else "флэт, сигналы слабее и чаще ложные"
        )
    )
    lines.append(
        f"- 📏 **ATR({config.ATR_PERIOD if config else 14}) = {t.atr:.8g}** — это {t.atr_pct:.2f}% цены: "
        "настолько монета ходит за свечу, от него считаются стоп и цели"
    )
    lines.append(f"- 🌡️ RSI {tr.rsi:.0f} — " + (
        "перегрев" if tr.rsi >= 70 else "перепроданность" if tr.rsi <= 30 else "нейтральная зона"
    ))
    if tr.ema_fast and tr.ema_slow:
        lines.append(
            f"- 📊 EMA20 {price(tr.ema_fast)} vs EMA50 {price(tr.ema_slow)} — "
            + ("быстрая выше медленной (бычья структура)" if tr.ema_fast > tr.ema_slow else "быстрая ниже медленной (медвежья структура)")
        )
    lines.append(f"- 💧 {acc.note}")
    if fib.retracements:
        lines.append(
            f"- 🎚️ Фибоначчи: свинг {price(fib.swing_low)} → {price(fib.swing_high)}; "
            f"0.382 = {price(fib.retracements.get('0.382'))}, "
            f"0.5 = {price(fib.retracements.get('0.5'))}, "
            f"0.618 = {price(fib.retracements.get('0.618'))}"
        )
        if fib.extensions:
            lines.append(
                f"- 🚀 Цели-расширения: 1.272 = {price(fib.extensions.get('1.272'))}, "
                f"1.618 = {price(fib.extensions.get('1.618'))}"
            )
    if t.vwap:
        lines.append(
            f"- ⚖️ VWAP {price(t.vwap)} — справедливая цена сессии; текущая "
            f"{'выше' if t.price >= t.vwap else 'ниже'} неё"
        )
    lines.append(f"- 🧮 Оценка блока: {t.score:.0f}/100")
    lines.append("")

    # ── 4. СОЦИАЛЬНЫЙ ФОН ────────────────────────────────────────
    s = report.social
    lines.append("## 📣 Социальный фон")
    lines.append(
        f"**Хайп: {s.hype_score:.0f}/100** за последние {s.window_hours} ч "
        f"({s.mentions} упоминаний, {s.unique_authors} авторов)"
    )
    lines.append("")
    lines.append(f"- 😊 Сентимент {s.sentiment:+.2f} (от −1 до +1)")
    if s.source == "x-api":
        lines.append("- 🐦 Источник: X (Twitter) API v2 — реальные упоминания")
    else:
        lines.append(
            "- ⚠️ **Эмуляция**: X API не подключён, хайп оценён по рыночным прокси "
            "(ускорение объёма, доля покупок, импульс цены)"
        )
    for post in s.top_posts[:3]:
        lines.append(f"- 💬 {post}")
    if s.ai_notes:
        lines.append(f"- 🤖 AI-скрининг: {s.ai_notes}")
    lines.append("")

    # ── 5. РЕКОМЕНДАЦИИ ──────────────────────────────────────────
    plan = report.plan
    lines.append("## 💡 Рекомендации")
    lines.append(
        f"**Позиция: {plan.direction}** | риск {report.risk_score}/10 | "
        f"R:R **1:{plan.rr:.1f}**"
    )
    lines.append("")
    if plan.direction == "WAIT":
        lines.append("- 🚫 Сделка не предлагается: нет подтверждённого направления или данные неполные")
        for why in plan.why:
            lines.append(f"- ℹ️ {why}")
    else:
        lines.append(
            f"- 🎯 Вход: **{price(plan.entry)}** (с учётом проскальзывания {report.micro.slippage_pct:.2f}%)"
        )
        atr_in_pct = plan.atr / plan.entry * 100 if plan.entry > 0 else 0.0
        sl_in_atr = abs(plan.entry - plan.stop_loss) / plan.atr if plan.atr > 0 else 0.0
        lines.append(
            f"- ⛔ Стоп-лосс: **{price(plan.stop_loss)}** — это {plan.atr_sl_pct:.2f}% от входа. "
            f"1 ATR = {plan.atr:.8g} ({atr_in_pct:.2f}% цены), стоп вынесен на {sl_in_atr:.1f}·ATR, "
            f"поэтому обычный шум свечи стоп не выбивает"
        )
        if plan.why:
            lines.append(f"- 📎 Обоснование стопа: {plan.why[0]}")
        for i, target in enumerate(plan.targets, 1):
            reward_pct = abs(target - plan.entry) / plan.entry * 100
            rr_i = abs(target - plan.entry) / max(abs(plan.entry - plan.stop_loss), 1e-12)
            lines.append(
                f"- ✅ Цель {i}: **{price(target)}** (+{reward_pct:.2f}%, R:R 1:{rr_i:.1f})"
            )
        lines.append(
            f"- 💰 Размер позиции: **{plan.position_pct:.2f}% депозита** = {usd(plan.position_usd)} "
            f"({plan.qty:.6f} шт.)"
        )
        lines.append(
            f"- ⚠️ Риск в деньгах: **{usd(plan.risk_usd)}** — столько теряете при срабатывании стопа"
        )
        lines.append(
            f"- 🎚️ Плечо ≤ {plan.leverage}x (подобрано по волатильности {t.atr_pct:.2f}% ATR)"
        )
        lines.append(f"- 🔁 Трейлинг-стоп после цели 1: {price(plan.trailing_stop)}")
        lines.append(f"- 🚨 Инвалидация сетапа: {plan.invalidation}")
        lines.append("")
        lines.append("**Почему такие числа:**")
        for why in plan.why:
            lines.append(f"- {why}")
    lines.append("")
    if report.reasons:
        lines.append("**Основания решения:**")
        for reason in report.reasons[:6]:
            lines.append(f"- {reason}")
        lines.append("")
    if report.risks:
        lines.append("**Риски:**")
        for risk in report.risks[:6]:
            lines.append(f"- ⚠️ {risk}")
        lines.append("")

    # ── качество данных ──────────────────────────────────────────
    lines.append("---")
    lines.append("### 🧾 Качество данных")
    lines.append(
        f"- Режим источника: `{report.token.source}` | время анализа {report.duration_sec:.1f}с"
    )
    if report.degraded:
        for item in report.degraded:
            lines.append(f"- ⚠️ {item}")
    else:
        lines.append("- ✅ Все проверки выполнены полностью")
    lines.append("")
    lines.append(
        "> ⚠️ Это аналитический отчёт, а не финансовая рекомендация. "
        "Криптовалюты — высокорисковый актив, возможна потеря всей суммы."
    )
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
#  ОТЧЁТ СКАНА
# ═══════════════════════════════════════════════════════════════
def render_scan(result: ScanResult, config: V2Config) -> str:
    """Markdown-сводка трёхуровневого скана."""
    lines: list[str] = [f"🔎 **СКАН РЫНКА** — режим `{result.mode}`, {result.duration_sec:.1f}с", ""]

    filters = config.filters_summary()
    lines.append(
        "Активные фильтры: "
        + ", ".join(f"{k}={'вкл' if v else 'выкл'}" for k, v in filters.items())
    )
    lines.append("")

    lines.append("## 🧮 Воронка")
    for stage in result.stages:
        lines.append(
            f"**Уровень {stage.level} — {stage.name}:** вошло {stage.entered} → "
            f"прошло **{stage.passed}** (отсеяно {stage.rejected}) за {stage.duration_sec:.1f}с"
        )
        for reason, count in list(stage.rejections.items())[:5]:
            lines.append(f"  - {reason} — {count}")
        for item in stage.degraded:
            lines.append(f"  - ⚠️ {item}")
        lines.append("")

    if result.reports:
        lines.append("## 🔥 Лучшие находки")
        for i, report in enumerate(result.reports[: config.SCAN_TOP_RESULTS], 1):
            token = report.token
            lines.append(
                f"{i}. **{token.symbol}** ({token.chain}) — {VERDICT_EMOJI[report.verdict]} "
                f"**{report.verdict_ru}** | оценка {report.score:.0f}/100 | риск {report.risk_score}/10 | "
                f"безопасность {report.security.score:.0f}/100 | "
                f"объём 5м {usd(token.volume_5m_usd)} | LP {usd(token.liquidity_usd)}"
            )
            if report.plan.direction != "WAIT":
                lines.append(
                    f"   → вход {price(report.plan.entry)}, стоп {price(report.plan.stop_loss)}, "
                    f"цель {price(report.plan.targets[0]) if report.plan.targets else '—'}, "
                    f"R:R 1:{report.plan.rr:.1f}"
                )
        lines.append("")
    elif result.survivors:
        lines.append("## 🔥 Прошли фильтры (без полного анализа)")
        for i, (token, security) in enumerate(result.survivors[: config.SCAN_TOP_RESULTS], 1):
            lines.append(
                f"{i}. **{token.symbol}** ({token.chain}) — безопасность {security.score:.0f}/100 | "
                f"объём 5м {usd(token.volume_5m_usd)} | {token.tx_5m} сделок за 5м | "
                f"LP {usd(token.liquidity_usd)}"
            )
        lines.append("")
    else:
        lines.append("⛔ Ничего не прошло фильтры. Это нормально: большую часть времени рынок пуст.")
        lines.append("")

    if result.errors:
        lines.append("## ⚠️ Ошибки во время скана")
        for error in result.errors[:10]:
            lines.append(f"- {error}")
        lines.append("")

    lines.append(
        "> Запросите разбор конкретной монеты: `analyze 0x…` или `analyze AURORA` — "
        "и получите полный отчёт с уровнями входа."
    )
    return "\n".join(lines)
