"""
Telegram-бот советника (aiogram).

Команды:
  /start, /help   — справка
  /watch          — наблюдение и текущие рекомендации
  /scan           — запустить скан скрытых монет
  /gems           — последние найденные монеты
  /signal SYMBOL  — полный анализ монеты с планом и графиком
  /chart SYMBOL   — только график
  /positions      — бумажные позиции по советам
  /fear           — Fear & Greed индекс
  /news           — последние новости
  /add SYMBOL     — добавить в наблюдение
  /del SYMBOL     — убрать из наблюдения
"""

from __future__ import annotations

import io

from aiogram import Bot, Dispatcher, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, Message

from src.config.settings import Settings
from src.core.logging import get_logger
from src.core.timeutil import fmt_ts, tf_label

logger = get_logger("notify.telegram")


def _fmt_analysis(res) -> str:
    """Текст анализа для Telegram (res — AnalysisResult или dict)."""
    d = res.to_dict() if hasattr(res, "to_dict") else res
    sym = d["symbol"]
    emoji = {"LONG": "📈", "SHORT": "📉"}.get(d["direction"], "⏸")
    lines = [
        f"{emoji} <b>{sym}</b> — {d['direction']} | рейтинг <b>{d['score']:.0f}/100</b> ({d['tier']})",
        f"Цена: <b>{d['price']:.8g}</b> (24ч: {d['price_24h_pct']:+.2f}%)",
        f"Уверенность: {d['confidence']*100:.0f}% | Волатильность: {d['volatility']['state_ru']} (ATR {d['volatility']['atr_pct']:.2f}%)",
        f"Тренд: {d['structure']['trend']} (ADX {d['structure']['adx']:.0f}) | RSI {d['momentum']['rsi']:.0f}",
    ]
    if d.get("plan"):
        p = d["plan"]
        zl, zh = p["entry_zone"]
        lines.append("")
        lines.append(f"🎯 <b>Вход:</b> {zl:.8g} – {zh:.8g}")
        lines.append(f"🛑 <b>Стоп:</b> {p['stop_loss']:.8g}")
        lines.append("✅ <b>Цели:</b> " + ", ".join(f"{t:.8g}" for t in p["targets"][:3]))
        lines.append(f"⚖️ <b>R:R</b> 1:{p['rr']:.1f} | Плечо ≤ {p['leverage']}x | Риск {p['position_pct']}%")
        lines.append(f"📏 До зоны входа: {p['distance_pct']:.1f}%")
    if d.get("reasons"):
        lines.append("")
        lines.append("<b>Почему:</b>")
        for r in d["reasons"][:5]:
            lines.append("  " + r)
    if d.get("risks"):
        lines.append("<b>Риски:</b>")
        for r in d["risks"][:3]:
            lines.append("  ⚠ " + r)
    if d.get("elliott", {}).get("pattern") != "unclear":
        lines.append(f"🌊 Волны: {d['elliott']['note']}")
    if d.get("funding_rate") is not None:
        lines.append(f"💧 Фандинг: {d['funding_rate']*100:.4f}% ({d.get('funding_trend','')})")
    if d.get("is_demo"):
        lines.append("")
        lines.append("⚠️ <i>ДЕМО-рынок (биржи недоступны) — это не реальный сигнал</i>")
    return "\n".join(lines)


class TelegramAdvisorBot:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.bot: Bot | None = None
        self.dp: Dispatcher | None = None
        self.router = Router()
        self.running = False
        if settings.TELEGRAM_BOT_TOKEN:
            self.bot = Bot(token=settings.TELEGRAM_BOT_TOKEN, default_parse_mode=ParseMode.HTML)
            self.dp = Dispatcher()
            self.dp.include_router(self.router)
            self._register_handlers()

    @property
    def enabled(self) -> bool:
        return self.bot is not None

    def _register_handlers(self) -> None:
        r = self.router

        @r.message(Command("start", "help"))
        async def _help(msg: Message) -> None:
            text = (
                "🤖 <b>HYPE Advisor</b> — аналитический советник\n\n"
                "Я мониторю рынок, ищу скрытые монеты, анализирую историю, "
                "волатильность, волны и структуру — и советую, когда входить и выходить.\n\n"
                "📌 <b>Команды:</b>\n"
                "/watch — текущее наблюдение и рекомендации\n"
                "/scan — запустить скан скрытых монет\n"
                "/gems — последние находки сканера\n"
                "/signal SYMBOL — полный анализ + график\n"
                "/chart SYMBOL — только график\n"
                "/positions — бумажные позиции\n"
                "/fear — Fear &amp; Greed\n"
                "/news — свежие новости\n"
                "/add SYMBOL, /del SYMBOL — управление наблюдением\n\n"
                "⚠️ Бот <b>не торгует</b> и ничего не гарантирует — это аналитика."
            )
            await msg.answer(text)

        @r.message(Command("watch"))
        async def _watch(msg: Message) -> None:
            text = await self._watch_text()
            await msg.answer(text or "Наблюдение пустое — добавьте монеты через /add")

        @r.message(Command("scan"))
        async def _scan(msg: Message) -> None:
            await msg.answer("🔎 Запускаю скан скрытых монет… (1–3 минуты)")
            text, chart = await self._scan_now()
            if chart:
                await msg.answer_photo(BufferedInputFile(chart, filename="gems.png"))
            await msg.answer(text)

        @r.message(Command("gems"))
        async def _gems(msg: Message) -> None:
            text = await self._gems_text()
            await msg.answer(text or "Сканер ещё не находил монет. Запустите /scan")

        @r.message(Command("signal", "chart"))
        async def _signal(msg: Message, command: CommandObject) -> None:
            sym = (command.args or "").strip().upper()
            if not sym:
                await msg.answer("Укажите монету: /signal SOLUSDT")
                return
            if not sym.endswith("USDT"):
                sym += "USDT"
            await msg.answer(f"⏳ Анализирую {sym}…")
            text, png = await self._signal(sym, want_chart=True)
            if png:
                await msg.answer_photo(BufferedInputFile(png, filename=f"{sym}.png"))
            await msg.answer(text)

        @r.message(Command("positions"))
        async def _positions(msg: Message) -> None:
            await msg.answer(self._positions_text())

        @r.message(Command("fear"))
        async def _fear(msg: Message) -> None:
            await msg.answer(await self._fear_text())

        @r.message(Command("news"))
        async def _news(msg: Message) -> None:
            await msg.answer(self._news_text())

        @r.message(Command("add"))
        async def _add(msg: Message, command: CommandObject) -> None:
            sym = (command.args or "").strip().upper()
            if not sym:
                await msg.answer("Укажите монету: /add SOLUSDT")
                return
            if not sym.endswith("USDT"):
                sym += "USDT"
            ctx_w = self._watcher()
            ctx_w.add_symbol(sym)
            await msg.answer(f"✅ {sym} добавлена в наблюдение")

        @r.message(Command("del"))
        async def _del(msg: Message, command: CommandObject) -> None:
            sym = (command.args or "").strip().upper()
            if not sym:
                await msg.answer("Укажите монету: /del SOLUSDT")
                return
            if not sym.endswith("USDT"):
                sym += "USDT"
            ctx_w = self._watcher()
            ctx_w.remove_symbol(sym)
            await msg.answer(f"🗑 {sym} убрана из наблюдения")

    # ── helpers ──
    def _ctx(self):
        from src.core.context import get_context

        return get_context()

    def _watcher(self):
        return self._ctx().watcher

    async def _watch_text(self) -> str:
        ctx = self._ctx()
        watcher = ctx.watcher
        results = watcher.get_results() if watcher else []
        if not results:
            return "Наблюдение пустое — добавьте монеты через /add"
        lines = [f"👁 <b>Наблюдение ({len(results)})</b> — {ctx.mode.upper()}\n"]
        for res in results[:15]:
            emoji = {"LONG": "📈", "SHORT": "📉"}.get(res.direction, "⏸")
            plan_txt = ""
            if res.plan:
                p = res.plan
                plan_txt = f" | вход {p.entry_zone[0]:.6g} | стоп {p.stop_loss:.6g} | R:R {p.rr:.1f}"
            lines.append(
                f"{emoji} <b>{res.symbol}</b> {res.score:.0f}/100 {res.direction} "
                f"(увер. {res.confidence*100:.0f}%){plan_txt}"
            )
        return "\n".join(lines)

    async def _scan_now(self) -> tuple[str, bytes | None]:
        ctx = self._ctx()
        ctx.ensure_services()
        report = await ctx.scanner.scan()
        lines = [
            f"🔎 <b>Скан завершён</b> ({report.mode.upper()}):",
            f"Инструментов: {report.total_instruments} | Кандидатов: {report.candidates} | Глубокий анализ: {report.analyzed}",
            f"Время: {report.duration_sec:.0f}с\n",
        ]
        if report.gems:
            lines.append("🔥 <b>Найденные монеты:</b>")
            for g in report.gems[:10]:
                emoji = {"LONG": "📈", "SHORT": "📉"}.get(g.get("direction", ""), "⭐")
                lines.append(
                    f"{emoji} <b>{g['symbol']}</b> — {g['score']:.0f}/100 ({g['tier']}) "
                    f"24ч: {g.get('price_24h_pct', 0):+.1f}% | {g.get('reason', '')[:70]}"
                )
        else:
            lines.append("Пока ничего выдающегося не найдено — рынок без явных аномалий.")
        png: bytes | None = None
        if report.gems:
            from src.charts.renderer import chart_gem_overview

            path = self._ctx().settings.chart_dir / "gems_overview.png"
            chart_gem_overview(report.gems, path)
            png = path.read_bytes()
        return "\n".join(lines), png

    async def _gems_text(self) -> str:
        ctx = self._ctx()
        gems = ctx.store.latest_gems(15)
        if not gems:
            return "Сканер ещё не находил монет. Запустите /scan"
        lines = ["💎 <b>Последние находки сканера</b>:\n"]
        for g in gems:
            lines.append(f"⭐ <b>{g['symbol']}</b> — {g['score']:.0f}/100 | {g.get('reason', '')[:80]}")
        return "\n".join(lines)

    async def _signal(self, symbol: str, want_chart: bool) -> tuple[str, bytes | None]:
        ctx = self._ctx()
        ctx.ensure_services()
        try:
            res = await ctx.engine.analyze(symbol, refresh=True)
        except Exception as e:  # noqa: BLE001
            return f"⚠️ Не удалось проанализировать {symbol}: {e}", None
        text = _fmt_analysis(res)
        png: bytes | None = None
        if want_chart:
            try:
                df = await ctx.source.get_klines(symbol, "15m", 300)
                from src.charts.renderer import chart_analysis
                from src.data.indicators import compute_all

                path = ctx.settings.chart_dir / f"{symbol}.png"
                chart_analysis(compute_all(df), res, path)
                png = path.read_bytes()
            except Exception as e:  # noqa: BLE001
                logger.warning("График %s не построен: %s", symbol, e)
        return text, png

    def _positions_text(self) -> str:
        ctx = self._ctx()
        positions = ctx.store.positions()
        if not positions:
            return "Открытых бумажных позиций нет. Советы с планом входа можно отслеживать вручную."
        lines = ["📊 <b>Бумажные позиции по советам</b>:\n"]
        for p in positions:
            st = {"open": "🟢 открыта", "closed": "⚪ закрыта"}.get(p["status"], p["status"])
            lines.append(
                f"{p['symbol']} {p['side']}: вход {p['entry']:.6g} | стоп {p['stop_loss']:.6g} | {st}"
            )
            if p["status"] == "closed" and p.get("pnl_pct") is not None:
                lines.append(f"   → PnL {p['pnl_pct']:+.2f}% ({p.get('note', '')})")
        return "\n".join(lines)

    async def _fear_text(self) -> str:
        ctx = self._ctx()
        ctx.ensure_services()
        try:
            fg = await ctx.source.get_fear_greed()
            emoji = {"Extreme Fear": "🟥", "Fear": "🟧", "Neutral": "🟨", "Greed": "🟩", "Extreme Greed": "🟩"}.get(
                fg.classification, "🟨"
            )
            advice = ""
            if fg.value < 25:
                advice = "Крайний страх — исторически зона накопления (контрарианский бычий фактор)."
            elif fg.value > 75:
                advice = "Крайняя жадность — осторожно, рынок перегрет."
            return f"{emoji} Fear & Greed: <b>{fg.value}/100</b> ({fg.classification})\n{advice}"
        except Exception as e:  # noqa: BLE001
            return f"Не удалось получить индекс: {e}"

    def _news_text(self) -> str:
        ctx = self._ctx()
        news = ctx.store.recent_news(10)
        if not news:
            return "Новостей пока нет — они появятся после первого скана."
        lines = ["📰 <b>Последние новости</b>:\n"]
        for n in news:
            sent = n.get("sentiment", 0.0)
            emoji = "🟢" if sent >= 0.15 else ("🔴" if sent <= -0.15 else "⚪")
            lines.append(f"{emoji} {n['title'][:100]}")
        return "\n".join(lines)

    # ── уведомления о сигналах ──
    async def send_alerts(self, alerts: list[dict], position_events: list[dict] | None = None) -> None:
        if not self.enabled:
            return
        chat_id = self.settings.TELEGRAM_ADMIN_CHAT_ID
        if not chat_id:
            logger.warning("TELEGRAM_CHAT_ID не задан — алерты не отправляются")
            return
        for a in alerts:
            try:
                await self.bot.send_message(chat_id, _fmt_analysis(a))
            except Exception as e:  # noqa: BLE001
                logger.warning("Алерт не отправлен: %s", e)
        for ev in position_events or []:
            try:
                await self.bot.send_message(
                    chat_id,
                    f"🔄 <b>{ev['symbol']}</b>: {ev['event']} @ {ev['price']:.8g}",
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("Событие позиции не отправлено: %s", e)

    # ── запуск/останов ──
    async def start_polling(self) -> None:
        if not self.enabled:
            logger.info("Telegram не настроен (нет TELEGRAM_BOT_TOKEN) — бот пропущен")
            return
        self.running = True
        logger.info("Telegram-бот запущен")
        await self.dp.start_polling(self.bot)

    async def stop(self) -> None:
        if self.enabled and self.dp:
            await self.dp.stop_polling()
        if self.bot:
            await self.bot.session.close()
        self.running = False
