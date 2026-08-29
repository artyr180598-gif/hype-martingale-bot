"""
Telegram-бот советника (aiogram 3) — управление кнопками.

Логика интерфейса взята из Freqtrade (custom keyboard + компактные команды)
и OctoBot (новичку не нужно знать команд): всё делается нажатием.

Главное меню → список монет → меню монеты:
  🔬 Полный анализ  — спектр по 5 таймфреймам и 8 группам факторов + вердикт
  🎯 План входа     — карточка сделки: объём в USDT/монетах, плечо,
                      пошаговая инструкция «что нажимать» в приложении биржи
  📉 График         — свечи с зоной входа, стопом и целями
  🌈 Спектр         — тепловая карта факторов
  ➕ В наблюдение   — алерты по этой монете

Настройки (депозит, риск %, плечо, биржа, рынок) меняются кнопками и
хранятся в SQLite отдельно для каждого чата.
"""

from __future__ import annotations

import json

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from src.analysis.advisor import BEGINNER_GUIDE, EXCHANGES, MARKETS, TradeAdvisor
from src.analysis.spectrum import GROUP_RU, SpectrumAnalyzer
from src.config.settings import Settings
from src.core.fmt import fmt_price, fmt_usd
from src.core.logging import get_logger
from src.core.timeutil import fmt_ts, tf_label

logger = get_logger("notify.telegram")

MAX_TEXT = 3900  # лимит Telegram 4096, оставляем запас на разметку


# ════════════════════════════════════════════════════════════════
#  НАСТРОЙКИ ПОЛЬЗОВАТЕЛЯ
# ════════════════════════════════════════════════════════════════
class Prefs:
    def __init__(self, settings: Settings, data: dict | None = None):
        data = data or {}
        self.deposit = float(data.get("deposit", settings.DEFAULT_DEPOSIT_USD))
        self.risk_pct = float(data.get("risk_pct", settings.RISK_PER_TRADE_PCT))
        self.leverage = data.get("leverage")  # None = авто по волатильности
        self.exchange = data.get("exchange", settings.DEFAULT_EXCHANGE)
        self.market = data.get("market", settings.DEFAULT_MARKET)
        if self.exchange not in EXCHANGES:
            self.exchange = "bybit"
        if self.market not in MARKETS:
            self.market = "futures"

    def to_dict(self) -> dict:
        return {
            "deposit": self.deposit,
            "risk_pct": self.risk_pct,
            "leverage": self.leverage,
            "exchange": self.exchange,
            "market": self.market,
        }

    def summary(self) -> str:
        lev = f"{self.leverage}x" if self.leverage else "авто по волатильности"
        market = "фьючерсы" if self.market == "futures" else "спот"
        return (
            f"💵 Депозит: <b>{fmt_usd(self.deposit)}</b>\n"
            f"⚖️ Риск на сделку: <b>{self.risk_pct:g}%</b> ({fmt_usd(self.deposit * self.risk_pct / 100)})\n"
            f"🎚 Плечо: <b>{lev}</b>\n"
            f"🏦 Биржа: <b>{self.exchange}</b>\n"
            f"📈 Рынок: <b>{market}</b>"
        )


# ════════════════════════════════════════════════════════════════
#  КЛАВИАТУРЫ
# ════════════════════════════════════════════════════════════════
def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 Наблюдение", callback_data="menu:watch"),
                InlineKeyboardButton(text="🔎 Скан рынка", callback_data="menu:scan"),
            ],
            [
                InlineKeyboardButton(text="💎 Находки", callback_data="menu:gems"),
                InlineKeyboardButton(text="🌡 Рынок", callback_data="menu:market"),
            ],
            [
                InlineKeyboardButton(text="📰 Новости", callback_data="menu:news"),
                InlineKeyboardButton(text="📚 Гайд новичку", callback_data="menu:guide"),
            ],
            [
                InlineKeyboardButton(text="🧪 Бэктест", callback_data="menu:backtest"),
                InlineKeyboardButton(text="⚙️ Настройки", callback_data="menu:prefs"),
            ],
            [InlineKeyboardButton(text="🧭 Меню", callback_data="menu:home")],
        ]
    )


def kb_watch(results: list) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for res in results[:24]:
        icon = {"LONG": "🟢", "SHORT": "🔴"}.get(res.direction, "⚪")
        row.append(InlineKeyboardButton(text=f"{icon} {res.symbol.replace('USDT','')} {res.score:.0f}", callback_data=f"sym:{res.symbol}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [
            InlineKeyboardButton(text="🔄 Обновить", callback_data="menu:watch"),
            InlineKeyboardButton(text="🧭 Меню", callback_data="menu:home"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_symbol(symbol: str, in_watch: bool) -> InlineKeyboardMarkup:
    watch_btn = (
        InlineKeyboardButton(text="➖ Убрать из наблюдения", callback_data=f"unwatch:{symbol}")
        if in_watch
        else InlineKeyboardButton(text="➕ В наблюдение", callback_data=f"watch:{symbol}")
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔬 Полный анализ", callback_data=f"deep:{symbol}"),
                InlineKeyboardButton(text="🎯 План входа", callback_data=f"plan:{symbol}"),
            ],
            [
                InlineKeyboardButton(text="📉 График", callback_data=f"chart:{symbol}"),
                InlineKeyboardButton(text="🌈 Спектр", callback_data=f"spectrum:{symbol}"),
            ],
            [InlineKeyboardButton(text="🧪 Бэктест", callback_data=f"backtest:{symbol}")],
            [watch_btn],
            [
                InlineKeyboardButton(text="📊 Наблюдение", callback_data="menu:watch"),
                InlineKeyboardButton(text="🧭 Меню", callback_data="menu:home"),
            ],
        ]
    )


def kb_gems(gems: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for g in gems[:18]:
        sym = g["symbol"]
        icon = {"LONG": "🟢", "SHORT": "🔴"}.get(g.get("direction", ""), "⭐")
        row.append(InlineKeyboardButton(text=f"{icon} {sym.replace('USDT','')} {g['score']:.0f}", callback_data=f"sym:{sym}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="🧭 Меню", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_prefs() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💵 Депозит", callback_data="set:deposit"),
                InlineKeyboardButton(text="⚖️ Риск %", callback_data="set:risk"),
            ],
            [
                InlineKeyboardButton(text="🎚 Плечо", callback_data="set:lev"),
                InlineKeyboardButton(text="🏦 Биржа", callback_data="set:exchange"),
            ],
            [
                InlineKeyboardButton(text="📈 Рынок: фьючерсы/спот", callback_data="set:market"),
            ],
            [InlineKeyboardButton(text="🧭 Меню", callback_data="menu:home")],
        ]
    )


def kb_lev() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Авто", callback_data="lev:auto"),
                InlineKeyboardButton(text="2x", callback_data="lev:2"),
                InlineKeyboardButton(text="3x", callback_data="lev:3"),
                InlineKeyboardButton(text="5x", callback_data="lev:5"),
            ],
            [
                InlineKeyboardButton(text="10x", callback_data="lev:10"),
                InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:prefs"),
            ],
        ]
    )


def kb_exchange() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Bybit", callback_data="exch:bybit"),
                InlineKeyboardButton(text="Binance", callback_data="exch:binance"),
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:prefs")],
        ]
    )


def kb_market() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Фьючерсы", callback_data="mkt:futures"),
                InlineKeyboardButton(text="Спот", callback_data="mkt:spot"),
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:prefs")],
        ]
    )


# ════════════════════════════════════════════════════════════════
#  ТЕКСТОВЫЕ БЛОКИ
# ════════════════════════════════════════════════════════════════
def _direction_icon(direction: str) -> str:
    return {"LONG": "🟢", "SHORT": "🔴"}.get(direction, "⏸")


def _short_card(res) -> str:
    d = res.to_dict()
    icon = _direction_icon(d["direction"])
    lines = [
        f"{icon} <b>{d['symbol']}</b> — {d['direction']} | рейтинг <b>{d['score']:.0f}/100</b> ({d['tier']})",
        f"Цена: <b>{d['price']:.8g}</b> ({d['price_24h_pct']:+.2f}% за 24ч)",
        f"Уверенность: {d['confidence']*100:.0f}% | Волатильность: {d['volatility']['state_ru']} "
        f"(ATR {d['volatility']['atr_pct']:.2f}%)",
        f"Тренд: {d['structure']['trend']} (ADX {d['structure']['adx']:.0f}) | RSI {d['momentum']['rsi']:.0f}",
    ]
    if d.get("plan"):
        p = d["plan"]
        lines += [
            "",
            f"🎯 Вход: <b>{p['entry_zone'][0]:.8g} – {p['entry_zone'][1]:.8g}</b>",
            f"🛑 Стоп: <b>{p['stop_loss']:.8g}</b>",
            f"✅ Цели: <b>{', '.join(f'{t:.8g}' for t in p['targets'][:3])}</b>",
            f"⚖️ R:R 1:{p['rr']:.1f} | плечо ≤ {p['leverage']}x | до зоны {p['distance_pct']:.1f}%",
        ]
    if d.get("reasons"):
        lines += ["", "<b>Почему:</b>"] + [f"  {r}" for r in d["reasons"][:5]]
    if d.get("risks"):
        lines += ["<b>Риски:</b>"] + [f"  ⚠ {r}" for r in d["risks"][:3]]
    if d.get("is_demo"):
        lines += ["", "<i>⚠️ Демо-рынок — это не реальный сигнал</i>"]
    return "\n".join(lines)


def _spectrum_text(rep) -> str:
    lines = [
        f"🌈 <b>СПЕКТРАЛЬНЫЙ АНАЛИЗ {rep.symbol}</b>",
        rep.summary,
        "",
        "<b>Спектр факторов:</b>",
        *rep.bars(),
        "",
        "<b>Таймфреймы:</b>",
    ]
    for t in rep.timeframes:
        icon = "🟢" if t.score > 0.15 else ("🔴" if t.score < -0.15 else "⚪")
        lines.append(f"{icon} <code>{tf_label(t.timeframe):<7} {t.score:+.2f}</code>  {t.note}")
    if rep.orderbook:
        ob = rep.orderbook
        lines += [
            "",
            "<b>Стакан:</b>",
            f"  Перекос: <b>{ob.get('imbalance', 0):+.2f}</b> "
            f"(биды {fmt_usd(ob.get('bid_usd', 0))} / аски {fmt_usd(ob.get('ask_usd', 0))})",
            f"  Спред: {ob.get('spread_pct', 0):.4f}%",
        ]
    if rep.derivatives.get("funding_rate") is not None:
        lines += [
            "",
            "<b>Деривативы:</b>",
            f"  Фандинг: <b>{rep.derivatives['funding_rate']*100:.4f}%</b>",
        ]
        if rep.derivatives.get("liquidations_buy_usd") is not None:
            lines.append(
                f"  Ликвидации: лонгов {fmt_usd(rep.derivatives['liquidations_buy_usd'])} / "
                f"шортов {fmt_usd(rep.derivatives['liquidations_sell_usd'])}"
            )
    if rep.market_context:
        mc = rep.market_context
        ctx_lines = ["", "<b>Контекст рынка:</b>"]
        if mc.get("fear_greed") is not None:
            ctx_lines.append(f"  Fear &amp; Greed: <b>{mc['fear_greed']}</b> ({mc.get('fear_greed_label', '')})")
        if mc.get("btc_4h_score") is not None:
            ctx_lines.append(f"  Тренд BTC (4h): <b>{mc['btc_4h_score']:+.2f}</b>")
        lines += ctx_lines
    if rep.news_count:
        lines += ["", f"📰 Новостей по монете: {rep.news_count}, сентимент {rep.news_sentiment:+.2f}"]

    # топ-5 сильнейших факторов
    ranked = sorted(rep.factors, key=lambda f: abs(f.value), reverse=True)[:6]
    if ranked:
        lines += ["", "<b>Сильнейшие факторы:</b>"]
        for f in ranked:
            icon = "🟢" if f.value > 0.15 else ("🔴" if f.value < -0.15 else "⚪")
            lines.append(f"{icon} {f.name}: <b>{f.value:+.2f}</b> — {f.detail}")
    return "\n".join(lines)


def _verdict_reconciliation(res, rep) -> str:
    """
    Спектр и движок плана считают независимо: спектр — взвешенная сумма
    факторов, план — жёсткий фильтр (≥62% совпавших факторов + тренд 4h).
    Расхождение нормально, но новичку его нужно объяснить.
    """
    plan_dir = res.direction
    spec_dir = rep.direction
    if plan_dir == spec_dir:
        return f"\n\n✅ Спектр и план входа совпадают: <b>{spec_dir}</b>."
    if plan_dir == "WAIT" and spec_dir in ("LONG", "SHORT"):
        return (
            f"\n\nℹ️ <b>Спектр склоняется к {spec_dir}</b> (сила {rep.total_score:+.2f}, "
            f"confluence {rep.confluence:.0f}/100), но жёсткий фильтр плана не пройден: "
            "нужно совпадение ≥62% факторов и тренд на 4h в ту же сторону. "
            "Поэтому конкретных уровней входа пока нет — это нормальная ситуация «рано»."
        )
    if spec_dir == "WAIT" and plan_dir in ("LONG", "SHORT"):
        return (
            f"\n\nℹ️ План входа даёт <b>{plan_dir}</b>, но спектр нейтрален "
            f"({rep.total_score:+.2f}) — перевес факторов небольшой. "
            "Рассматривай уменьшенный объём или жди подтверждения."
        )
    return (
        f"\n\n⚠️ <b>Конфликт вердиктов:</b> план — {plan_dir}, спектр — {spec_dir}. "
        "При таком расхождении правильная позиция — не входить и дождаться согласованности."
    )


def _watch_text(ctx, results: list) -> str:
    if not results:
        return (
            "👁 <b>Наблюдение пустое</b>\n\n"
            "Добавь монеты командой /add BTC или кнопкой «➕ В наблюдение» на карточке монеты."
        )
    lines = [f"👁 <b>Наблюдение ({len(results)})</b> — источник {ctx.mode.upper()}\n", "Выбери монету:"]
    for res in results[:24]:
        icon = _direction_icon(res.direction)
        plan_txt = ""
        if res.plan:
            plan_txt = f" | вход {res.plan.entry_zone[0]:.6g} | стоп {res.plan.stop_loss:.6g} | R:R {res.plan.rr:.1f}"
        lines.append(
            f"{icon} <b>{res.symbol}</b> {res.score:.0f}/100 {res.direction} "
            f"(увер. {res.confidence*100:.0f}%){plan_txt}"
        )
    return "\n".join(lines)


def _split(text: str) -> list[str]:
    """Режет длинный текст по границам строк под лимит Telegram."""
    if len(text) <= MAX_TEXT:
        return [text]
    chunks: list[str] = []
    cur = ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > MAX_TEXT:
            chunks.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        chunks.append(cur)
    return chunks


# ════════════════════════════════════════════════════════════════
#  БОТ
# ════════════════════════════════════════════════════════════════
class Form(StatesGroup):
    deposit = State()
    risk = State()
    backtest = State()


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

    # ── контекст и настройки ──
    def _ctx(self):
        from src.core.context import get_context

        ctx = get_context()
        ctx.ensure_services()
        return ctx

    def _watcher(self):
        return self._ctx().watcher

    def _prefs(self, chat_id: int) -> Prefs:
        raw = self._ctx().store.get_state(f"prefs:{chat_id}", "")
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {}
        return Prefs(self.settings, data)

    def _save_prefs(self, chat_id: int, prefs: Prefs) -> None:
        self._ctx().store.set_state(f"prefs:{chat_id}", json.dumps(prefs.to_dict()))

    def _in_watch(self, symbol: str) -> bool:
        w = self._watcher()
        return bool(w and symbol in w.watchlist)

    async def _analyze(self, symbol: str, refresh: bool = True):
        return await self._ctx().engine.analyze(symbol, refresh=refresh)

    async def _spectrum(self, symbol: str):
        ctx = self._ctx()
        news = []
        try:
            raw = ctx.store.recent_news(30)
            from src.data.models import NewsItem

            news = [
                NewsItem(
                    id=str(n["id"]), ts_ms=n["ts_ms"], source=n.get("source", ""),
                    title=n["title"], url=n.get("url", ""),
                    symbols=n.get("symbols", []), sentiment=float(n.get("sentiment", 0.0)),
                )
                for n in raw
            ]
        except Exception as e:  # noqa: BLE001
            logger.debug("Новости для спектра недоступны: %s", e)
        analyzer = SpectrumAnalyzer(ctx.source, self.settings)
        return await analyzer.analyze(symbol, news)

    async def _chart_png(self, symbol: str) -> bytes | None:
        ctx = self._ctx()
        try:
            df = await ctx.source.get_klines(symbol, "15m", 300)
            res = await ctx.engine.analyze(symbol)
            from src.charts.renderer import chart_analysis
            from src.data.indicators import compute_all

            path = ctx.settings.chart_dir / f"{symbol}.png"
            chart_analysis(compute_all(df), res, path)
            return path.read_bytes()
        except Exception as e:  # noqa: BLE001
            logger.warning("График %s не построен: %s", symbol, e)
            return None

    async def _spectrum_png(self, symbol: str) -> bytes | None:
        try:
            rep = await self._spectrum(symbol)
            from src.charts.spectrum import chart_spectrum

            path = self._ctx().settings.chart_dir / f"{symbol}_spectrum.png"
            chart_spectrum(rep, path)
            return path.read_bytes()
        except Exception as e:  # noqa: BLE001
            logger.warning("Спектр-график %s не построен: %s", symbol, e)
            return None

    async def _card(self, symbol: str, chat_id: int):
        ctx = self._ctx()
        res = await self._analyze(symbol)
        prefs = self._prefs(chat_id)
        advisor = TradeAdvisor(ctx.source, self.settings)
        return await advisor.build(
            res,
            deposit_usd=prefs.deposit,
            risk_pct=prefs.risk_pct,
            leverage=prefs.leverage,
            exchange=prefs.exchange,
            market=prefs.market,
        )

    # ── отправка с защитой от ошибок разметки ──
    async def _run_backtest(self, symbol: str, days: float = 30.0):
        """Прогон советника по истории. Бросает AdvisorError при нехватке данных."""
        from src.backtest.engine import BacktestConfig
        from src.backtest.service import run_backtest

        ctx = self._ctx()
        cfg = BacktestConfig(entry_tf="1h", medium_tf="4h", macro_tf="1d", warmup_bars=200)
        return await run_backtest(ctx.source, ctx.engine, symbol, cfg, period_days=days)

    async def _send(self, msg: Message | CallbackQuery, text: str, kb: InlineKeyboardMarkup | None = None) -> None:
        target = msg.message if isinstance(msg, CallbackQuery) else msg
        for chunk in _split(text):
            try:
                await target.answer(chunk, reply_markup=kb)
            except TelegramBadRequest:
                # разметка HTML не сошлась — шлём чистым текстом
                safe = chunk.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
                safe = safe.replace("<code>", "").replace("</code>", "")
                await target.answer(safe, reply_markup=kb)

    # ────────────────────────────────────────────────────────────
    #  ХЕНДЛЕРЫ
    # ────────────────────────────────────────────────────────────
    def _register_handlers(self) -> None:  # noqa: C901
        r = self.router

        # ── старт и справка ──
        @r.message(Command("start", "help"))
        async def _start(msg: Message) -> None:
            ctx = self._ctx()
            try:
                await ctx.ensure_ready()
            except Exception as e:  # noqa: BLE001
                logger.warning("Источник данных не определён: %s", e)
            text = (
                f"🤖 <b>{self.settings.APP_NAME}</b> — профессиональный крипто-советник\n\n"
                f"Я собираю данные с биржи (источник: <b>{ctx.mode.upper()}</b>), считаю 60+ индикаторов "
                "на 5 таймфреймах, смотрю стакан, фандинг, ликвидации и новости — "
                "и выдаю готовый план: куда входить, где стоп, где цели и сколько купить.\n\n"
                "<b>Начни с кнопки «📊 Наблюдение»</b> — там список монет. "
                "Нажми на монету, затем «🔬 Полный анализ» или «🎯 План входа».\n\n"
                "⚙️ В настройках укажи свой депозит — тогда я посчитаю объём позиции в USDT и в монетах.\n\n"
                "⚠️ Бот <b>не торгует</b> и не имеет доступа к твоему счёту. Это аналитика, не гарантия прибыли."
            )
            await msg.answer(text, reply_markup=kb_main())

        # ── команды для совместимости ──
        @r.message(Command("watch"))
        async def _watch_cmd(msg: Message) -> None:
            await self._show_watch(msg)

        @r.message(Command("scan"))
        async def _scan_cmd(msg: Message) -> None:
            await self._run_scan(msg)

        @r.message(Command("gems"))
        async def _gems_cmd(msg: Message) -> None:
            await self._show_gems(msg)

        @r.message(Command("signal", "analyze"))
        async def _signal_cmd(msg: Message, command: CommandObject) -> None:
            sym = _norm_symbol(command.args)
            if not sym:
                await msg.answer("Укажи монету: /signal SOL или /signal SOLUSDT")
                return
            await self._full_analysis(msg, sym)

        @r.message(Command("chart"))
        async def _chart_cmd(msg: Message, command: CommandObject) -> None:
            sym = _norm_symbol(command.args)
            if not sym:
                await msg.answer("Укажи монету: /chart SOL")
                return
            await msg.answer(f"⏳ Строю график {sym}…")
            png = await self._chart_png(sym)
            if png:
                await msg.answer_photo(BufferedInputFile(png, filename=f"{sym}.png"))
            else:
                await msg.answer("Не удалось построить график.")

        @r.message(Command("positions"))
        async def _positions(msg: Message) -> None:
            await msg.answer(self._positions_text())

        @r.message(Command("fear"))
        async def _fear(msg: Message) -> None:
            await msg.answer(await self._market_text())

        @r.message(Command("news"))
        async def _news(msg: Message) -> None:
            await msg.answer(self._news_text())

        @r.message(Command("guide"))
        async def _guide(msg: Message) -> None:
            await msg.answer(BEGINNER_GUIDE, reply_markup=kb_main())

        @r.message(Command("add"))
        async def _add(msg: Message, command: CommandObject) -> None:
            sym = _norm_symbol(command.args)
            if not sym:
                await msg.answer("Укажи монету: /add SOL")
                return
            self._watcher().add_symbol(sym)
            await msg.answer(f"✅ {sym} добавлена в наблюдение", reply_markup=kb_main())

        @r.message(Command("del"))
        async def _del(msg: Message, command: CommandObject) -> None:
            sym = _norm_symbol(command.args)
            if not sym:
                await msg.answer("Укажи монету: /del SOL")
                return
            self._watcher().remove_symbol(sym)
            await msg.answer(f"🗑 {sym} убрана из наблюдения", reply_markup=kb_main())

        @r.message(Command("deposit"))
        async def _deposit(msg: Message, command: CommandObject) -> None:
            try:
                value = float((command.args or "").replace(",", "."))
            except ValueError:
                await msg.answer("Формат: /deposit 500", reply_markup=kb_main())
                return
            prefs = self._prefs(msg.chat.id)
            prefs.deposit = max(1.0, value)
            self._save_prefs(msg.chat.id, prefs)
            await msg.answer(f"💵 Депозит: {fmt_usd(prefs.deposit)}", reply_markup=kb_prefs())

        # ── ГЛАВНОЕ МЕНЮ (кнопки) ──
        @r.callback_query(F.data == "menu:home")
        async def _home(call: CallbackQuery) -> None:
            await call.answer()
            await self._send(call, "🧭 <b>Главное меню</b>\n\nВыбери раздел:", kb_main())

        @r.callback_query(F.data == "menu:watch")
        async def _watch_btn(call: CallbackQuery) -> None:
            await call.answer("Обновляю наблюдение…")
            await self._show_watch(call)

        @r.callback_query(F.data == "menu:scan")
        async def _scan_btn(call: CallbackQuery) -> None:
            await call.answer("Запускаю скан рынка")
            await self._run_scan(call)

        @r.callback_query(F.data == "menu:gems")
        async def _gems_btn(call: CallbackQuery) -> None:
            await call.answer()
            await self._show_gems(call)

        @r.callback_query(F.data == "menu:market")
        async def _market_btn(call: CallbackQuery) -> None:
            await call.answer()
            await self._send(call, await self._market_text(), kb_main())

        @r.callback_query(F.data == "menu:news")
        async def _news_btn(call: CallbackQuery) -> None:
            await call.answer()
            await self._send(call, self._news_text(), kb_main())

        @r.callback_query(F.data == "menu:guide")
        async def _guide_btn(call: CallbackQuery) -> None:
            await call.answer()
            await self._send(call, BEGINNER_GUIDE, kb_main())

        @r.callback_query(F.data == "menu:prefs")
        async def _prefs_btn(call: CallbackQuery) -> None:
            await call.answer()
            prefs = self._prefs(call.message.chat.id)
            await self._send(call, "⚙️ <b>Настройки сделки</b>\n\n" + prefs.summary(), kb_prefs())

        # ── настройка значений ──
        @r.callback_query(F.data == "set:deposit")
        async def _set_deposit(call: CallbackQuery, state: FSMContext) -> None:
            await call.answer()
            await state.set_state(Form.deposit)
            await self._send(call, "💵 Введи сумму депозита в USDT (например <code>500</code>):")

        @r.callback_query(F.data == "set:risk")
        async def _set_risk(call: CallbackQuery, state: FSMContext) -> None:
            await call.answer()
            await state.set_state(Form.risk)
            await self._send(call, "⚖️ Введи риск на сделку в % (рекомендую <code>1</code>, максимум 2):")

        @r.message(Form.deposit)
        async def _get_deposit(msg: Message, state: FSMContext) -> None:
            try:
                value = float(msg.text.replace(",", ".").replace("$", "").strip())
            except (ValueError, AttributeError):
                await msg.answer("Нужно число, например 500")
                return
            prefs = self._prefs(msg.chat.id)
            prefs.deposit = max(1.0, value)
            self._save_prefs(msg.chat.id, prefs)
            await state.clear()
            await msg.answer(
                f"✅ Депозит: <b>{fmt_usd(prefs.deposit)}</b>\n"
                f"Риск 1% = {fmt_usd(prefs.deposit * 0.01)} на сделку",
                reply_markup=kb_prefs(),
            )

        @r.message(Form.risk)
        async def _get_risk(msg: Message, state: FSMContext) -> None:
            try:
                value = float(msg.text.replace(",", ".").replace("%", "").strip())
            except (ValueError, AttributeError):
                await msg.answer("Нужно число, например 1")
                return
            value = min(max(value, 0.1), 5.0)
            prefs = self._prefs(msg.chat.id)
            prefs.risk_pct = value
            self._save_prefs(msg.chat.id, prefs)
            await state.clear()
            note = "" if value <= 2 else "\n⚠️ Больше 2% — агрессивный риск, легко слить депозит."
            await msg.answer(
                f"✅ Риск: <b>{value:g}%</b> = {fmt_usd(prefs.deposit * value / 100)} на сделку{note}",
                reply_markup=kb_prefs(),
            )

        @r.callback_query(F.data == "set:lev")
        async def _set_lev(call: CallbackQuery) -> None:
            await call.answer()
            await self._send(call, "🎚 <b>Плечо</b>\n\nНовичку советую 2–3x или «Авто»:", kb_lev())

        @r.callback_query(F.data.startswith("lev:"))
        async def _pick_lev(call: CallbackQuery) -> None:
            raw = call.data.split(":", 1)[1]
            prefs = self._prefs(call.message.chat.id)
            prefs.leverage = None if raw == "auto" else int(raw)
            self._save_prefs(call.message.chat.id, prefs)
            await call.answer("Сохранено")
            await self._send(call, "⚙️ <b>Настройки сделки</b>\n\n" + prefs.summary(), kb_prefs())

        @r.callback_query(F.data == "set:exchange")
        async def _set_exchange(call: CallbackQuery) -> None:
            await call.answer()
            await self._send(call, "🏦 Под какую биржу писать пошаговую инструкцию?", kb_exchange())

        @r.callback_query(F.data.startswith("exch:"))
        async def _pick_exchange(call: CallbackQuery) -> None:
            exch = call.data.split(":", 1)[1]
            prefs = self._prefs(call.message.chat.id)
            prefs.exchange = exch
            self._save_prefs(call.message.chat.id, prefs)
            await call.answer("Сохранено")
            await self._send(call, "⚙️ <b>Настройки сделки</b>\n\n" + prefs.summary(), kb_prefs())

        @r.callback_query(F.data == "set:market")
        async def _set_market(call: CallbackQuery) -> None:
            await call.answer()
            await self._send(
                call,
                "📈 <b>Рынок</b>\n\n<b>Фьючерсы</b>: можно шортить, есть плечо и ликвидация.\n"
                "<b>Спот</b>: покупаешь саму монету, ликвидации нет, шорт недоступен.",
                kb_market(),
            )

        @r.callback_query(F.data.startswith("mkt:"))
        async def _pick_market(call: CallbackQuery) -> None:
            market = call.data.split(":", 1)[1]
            prefs = self._prefs(call.message.chat.id)
            prefs.market = market
            self._save_prefs(call.message.chat.id, prefs)
            await call.answer("Сохранено")
            await self._send(call, "⚙️ <b>Настройки сделки</b>\n\n" + prefs.summary(), kb_prefs())

        # ── монеты ──
        @r.callback_query(F.data.startswith("sym:"))
        async def _symbol_menu(call: CallbackQuery) -> None:
            sym = call.data.split(":", 1)[1]
            await call.answer()
            try:
                res = await self._analyze(sym, refresh=False)
            except Exception as e:  # noqa: BLE001
                await self._send(call, f"⚠️ Не удалось получить {sym}: {e}", kb_main())
                return
            text = (
                f"<b>{res.symbol}</b> — {_direction_icon(res.direction)} {res.direction} | "
                f"рейтинг <b>{res.score:.0f}/100</b> ({res.tier})\n"
                f"Цена: <b>{res.price:.8g}</b> ({res.price_24h_pct:+.2f}% за 24ч)\n"
                f"Уверенность: {res.confidence*100:.0f}%\n\n"
                "Выбери, что показать:"
            )
            await self._send(call, text, kb_symbol(sym, self._in_watch(sym)))

        @r.callback_query(F.data.startswith("deep:"))
        async def _deep(call: CallbackQuery) -> None:
            sym = call.data.split(":", 1)[1]
            await call.answer("Полный анализ: 5 таймфреймов, 60+ индикаторов…")
            await self._full_analysis(call, sym)

        @r.callback_query(F.data.startswith("plan:"))
        async def _plan(call: CallbackQuery) -> None:
            sym = call.data.split(":", 1)[1]
            await call.answer("Считаю объём позиции и уровни…")
            await self._show_plan(call, sym)

        @r.callback_query(F.data.startswith("chart:"))
        async def _chart(call: CallbackQuery) -> None:
            sym = call.data.split(":", 1)[1]
            await call.answer("Строю график…")
            png = await self._chart_png(sym)
            if png:
                await call.message.answer_photo(BufferedInputFile(png, filename=f"{sym}.png"))
            else:
                await self._send(call, "Не удалось построить график.", kb_symbol(sym, self._in_watch(sym)))

        @r.callback_query(F.data.startswith("spectrum:"))
        async def _spectrum_btn(call: CallbackQuery) -> None:
            sym = call.data.split(":", 1)[1]
            await call.answer("Собираю спектр…")
            try:
                rep = await self._spectrum(sym)
            except Exception as e:  # noqa: BLE001
                await self._send(call, f"⚠️ Спектр не собран: {e}", kb_symbol(sym, self._in_watch(sym)))
                return
            await self._send(call, _spectrum_text(rep), kb_symbol(sym, self._in_watch(sym)))
            png = await self._spectrum_png(sym)
            if png:
                await call.message.answer_photo(BufferedInputFile(png, filename=f"{sym}_spectrum.png"))

        @r.callback_query(F.data.startswith("backtest:"))
        async def _backtest_btn(call: CallbackQuery) -> None:
            sym = _norm_symbol(call.data.split(":", 1)[1])
            await call.answer("Считаю бэктест, это может занять минуту…")
            await self._send(call, f"🧪 Считаю бэктест <b>{sym}</b> по истории за 30 дней…\n"
                                   f"Прогоняю каждый часовой бар через тот же анализ, что и вживую.")
            try:
                from src.backtest.report import backtest_report

                res = await self._run_backtest(sym)
            except Exception as e:  # noqa: BLE001
                await self._send(call, f"⚠️ Бэктест не получился: {e}",
                                 kb_symbol(sym, self._in_watch(sym)))
                return
            await self._send(call, backtest_report(res), kb_symbol(sym, self._in_watch(sym)))

        @r.callback_query(F.data == "menu:backtest")
        async def _backtest_menu(call: CallbackQuery, state: FSMContext) -> None:
            await call.answer()
            await state.set_state(Form.backtest)
            await self._send(call, "🧪 <b>Бэктест</b>\n\n"
                                   "Пришлите тикер монеты, например <code>BTCUSDT</code>.\n"
                                   "Прогоню советника по истории за 30 дней и покажу, "
                                   "сколько бы он заработал или потерял.")

        @r.message(Form.backtest)
        async def _backtest_symbol(msg: Message, state: FSMContext) -> None:
            await state.clear()
            sym = _norm_symbol(msg.text or "")
            if not sym:
                await self._send(msg, "⚠️ Не понял тикер. Пример: <code>BTCUSDT</code>", kb_main())
                return
            await self._send(msg, f"🧪 Считаю бэктест <b>{sym}</b> за 30 дней…", kb_main())
            try:
                from src.backtest.report import backtest_report

                res = await self._run_backtest(sym)
            except Exception as e:  # noqa: BLE001
                await self._send(msg, f"⚠️ Бэктест не получился: {e}", kb_main())
                return
            await self._send(msg, backtest_report(res), kb_symbol(sym, self._in_watch(sym)))

        @r.callback_query(F.data.startswith("watch:"))
        async def _watch_add(call: CallbackQuery) -> None:
            sym = call.data.split(":", 1)[1]
            self._watcher().add_symbol(sym)
            await call.answer(f"{sym} в наблюдении")
            await self._send(call, f"✅ <b>{sym}</b> добавлена в наблюдение", kb_symbol(sym, True))

        @r.callback_query(F.data.startswith("unwatch:"))
        async def _watch_del(call: CallbackQuery) -> None:
            sym = call.data.split(":", 1)[1]
            self._watcher().remove_symbol(sym)
            await call.answer("Убрано из наблюдения")
            await self._send(call, f"🗑 <b>{sym}</b> убрана из наблюдения", kb_symbol(sym, False))

    # ────────────────────────────────────────────────────────────
    #  ДЕЙСТВИЯ
    # ────────────────────────────────────────────────────────────
    async def _show_watch(self, msg: Message | CallbackQuery) -> None:
        ctx = self._ctx()
        watcher = ctx.watcher
        results = watcher.get_results() if watcher else []
        if not results:
            # первый запуск: прогоняем цикл наблюдения
            try:
                await watcher.run_cycle()
                results = watcher.get_results()
            except Exception as e:  # noqa: BLE001
                logger.warning("Цикл наблюдения не удался: %s", e)
        await self._send(msg, _watch_text(ctx, results), kb_watch(results))

    async def _run_scan(self, msg: Message | CallbackQuery) -> None:
        target = msg.message if isinstance(msg, CallbackQuery) else msg
        await self._send(msg, "🔎 Запускаю скан рынка (1–3 минуты)…")
        try:
            report = await self._ctx().scanner.scan()
        except Exception as e:  # noqa: BLE001
            await self._send(msg, f"⚠️ Скан не удался: {e}", kb_main())
            return
        lines = [
            f"🔎 <b>Скан завершён</b> (источник {report.mode.upper()})",
            f"Инструментов: {report.total_instruments} | кандидатов: {report.candidates} | "
            f"глубокий анализ: {report.analyzed} | {report.duration_sec:.0f}с",
        ]
        if report.gems:
            lines += ["", "🔥 <b>Найденные монеты:</b>"]
            for g in report.gems[:10]:
                icon = _direction_icon(g.get("direction", ""))
                lines.append(
                    f"{icon} <b>{g['symbol']}</b> — {g['score']:.0f}/100 ({g['tier']}) "
                    f"{g.get('price_24h_pct', 0):+.1f}% | {str(g.get('reason', ''))[:60]}"
                )
        else:
            lines.append("Пока ничего выдающегося: рынок без явных аномалий.")
        await self._send(msg, "\n".join(lines), kb_gems(report.gems))
        if report.gems:
            try:
                from src.charts.renderer import chart_gem_overview

                path = self._ctx().settings.chart_dir / "gems_overview.png"
                chart_gem_overview(report.gems, path)
                await target.answer_photo(BufferedInputFile(path.read_bytes(), filename="gems.png"))
            except Exception as e:  # noqa: BLE001
                logger.debug("Панель находок не построена: %s", e)

    async def _show_gems(self, msg: Message | CallbackQuery) -> None:
        gems = self._ctx().store.latest_gems(18)
        if not gems:
            await self._send(msg, "Сканер ещё не находил монет. Нажми «🔎 Скан рынка».", kb_main())
            return
        lines = ["💎 <b>Последние находки сканера</b>\n", "Нажми на монету, чтобы открыть карточку:"]
        await self._send(msg, "\n".join(lines), kb_gems(gems))

    async def _full_analysis(self, msg: Message | CallbackQuery, symbol: str) -> None:
        """Одна кнопка → весь анализ: вердикт, спектр, графики, карточка сделки."""
        target = msg.message if isinstance(msg, CallbackQuery) else msg
        symbol = _norm_symbol(symbol) or symbol
        try:
            res = await self._analyze(symbol, refresh=True)
        except Exception as e:  # noqa: BLE001
            await self._send(msg, f"⚠️ Не удалось проанализировать {symbol}: {e}", kb_main())
            return

        await self._send(msg, _short_card(res), kb_symbol(symbol, self._in_watch(symbol)))

        png = await self._chart_png(symbol)
        if png:
            await target.answer_photo(BufferedInputFile(png, filename=f"{symbol}.png"))

        try:
            rep = await self._spectrum(symbol)
            await self._send(msg, _spectrum_text(rep) + _verdict_reconciliation(res, rep))
            spng = await self._spectrum_png(symbol)
            if spng:
                await target.answer_photo(BufferedInputFile(spng, filename=f"{symbol}_spectrum.png"))
        except Exception as e:  # noqa: BLE001
            logger.warning("Спектр %s не собран: %s", symbol, e)

        await self._show_plan(msg, symbol, with_keyboard=False)
        await self._send(msg, "Что показать ещё?", kb_symbol(symbol, self._in_watch(symbol)))

    async def _show_plan(
        self, msg: Message | CallbackQuery, symbol: str, with_keyboard: bool = True
    ) -> None:
        chat_id = msg.message.chat.id if isinstance(msg, CallbackQuery) else msg.chat.id
        try:
            card = await self._card(symbol, chat_id)
        except Exception as e:  # noqa: BLE001
            logger.exception("Карточка сделки %s не собрана", symbol)
            await self._send(msg, f"⚠️ Не удалось собрать план: {e}", kb_main())
            return
        kb = kb_symbol(symbol, self._in_watch(symbol)) if with_keyboard else None
        await self._send(msg, card.to_text(), kb)

    # ── текстовые блоки ──
    async def _market_text(self) -> str:
        ctx = self._ctx()
        lines = ["🌡 <b>Состояние рынка</b>"]
        try:
            fg = await ctx.source.get_fear_greed()
            icon = {
                "Extreme Fear": "🟥", "Fear": "🟧", "Neutral": "🟨",
                "Greed": "🟩", "Extreme Greed": "🟩",
            }.get(fg.classification, "🟨")
            lines.append(f"{icon} Fear &amp; Greed: <b>{fg.value}/100</b> ({fg.classification})")
            if fg.value < 25:
                lines.append("Крайний страх — исторически зона накопления.")
            elif fg.value > 75:
                lines.append("Крайняя жадность — рынок перегрет, входи осторожно.")
        except Exception as e:  # noqa: BLE001
            lines.append(f"Fear &amp; Greed недоступен: {e}")
        try:
            g = await ctx.source.get_global_stats()
            lines.append(f"Капитализация рынка: <b>{fmt_usd(g.total_market_cap_usd)}</b> ({g.market_cap_change_24h_pct:+.2f}%)")
            lines.append(f"Объём за 24ч: {fmt_usd(g.total_volume_24h_usd)}")
            lines.append(f"Доминация BTC: {g.btc_dominance:.1f}% | ETH: {g.eth_dominance:.1f}%")
        except Exception as e:  # noqa: BLE001
            logger.debug("Глобальная статистика недоступна: %s", e)
        results = ctx.watcher.get_results() if ctx.watcher else []
        longs = [r for r in results if r.direction == "LONG"]
        shorts = [r for r in results if r.direction == "SHORT"]
        if results:
            lines.append(f"Наблюдение: {len(results)} монет | LONG {len(longs)} | SHORT {len(shorts)} | WAIT {len(results)-len(longs)-len(shorts)}")
        lines.append(f"\nИсточник данных: <b>{ctx.mode.upper()}</b>")
        return "\n".join(lines)

    def _news_text(self) -> str:
        news = self._ctx().store.recent_news(12)
        if not news:
            return "📰 Новостей пока нет — они появятся после первого скана (/scan)."
        lines = ["📰 <b>Последние новости</b>\n"]
        for n in news:
            sent = float(n.get("sentiment", 0.0))
            icon = "🟢" if sent >= 0.15 else ("🔴" if sent <= -0.15 else "⚪")
            lines.append(f"{icon} {n['title'][:110]}")
        return "\n".join(lines)

    def _positions_text(self) -> str:
        positions = self._ctx().store.positions()
        if not positions:
            return "Открытых бумажных позиций нет.\n\nОткрой сделку по карточке «🎯 План входа» — и я буду следить за стопом и целями."
        lines = ["📊 <b>Позиции по советам</b>\n"]
        for p in positions:
            status = {"open": "🟢 открыта", "closed": "⚪ закрыта"}.get(p["status"], p["status"])
            lines.append(f"{p['symbol']} {p['side']}: вход {p['entry']:.6g} | стоп {p['stop_loss']:.6g} | {status}")
            if p["status"] == "closed" and p.get("pnl_pct") is not None:
                lines.append(f"   → PnL {p['pnl_pct']:+.2f}% ({p.get('note', '')})")
        return "\n".join(lines)

    # ── алерты ──
    async def send_alerts(self, alerts: list[dict], position_events: list[dict] | None = None) -> None:
        if not self.enabled:
            return
        chat_id = self.settings.TELEGRAM_ADMIN_CHAT_ID
        if not chat_id:
            logger.warning("TELEGRAM_ADMIN_CHAT_ID не задан — алерты не отправляются")
            return
        for a in alerts:
            try:
                sym = a.get("symbol", "")
                for chunk in _split(_short_card(a)):
                    await self.bot.send_message(chat_id, chunk, reply_markup=kb_symbol(sym, True))
            except Exception as e:  # noqa: BLE001
                logger.warning("Алерт не отправлен: %s", e)
        for ev in position_events or []:
            try:
                text = {
                    "stop_loss": f"🛑 <b>{ev['symbol']}</b>: стоп-лосс сработал @ {ev['price']:.8g}",
                    "target_1": f"✅ <b>{ev['symbol']}</b>: цель 1 достигнута @ {ev['price']:.8g}",
                }.get(ev["event"], f"🔄 <b>{ev['symbol']}</b>: {ev['event']} @ {ev['price']:.8g}")
                await self.bot.send_message(chat_id, text, reply_markup=kb_symbol(ev["symbol"], True))
            except Exception as e:  # noqa: BLE001
                logger.warning("Событие позиции не отправлено: %s", e)

    # ── запуск ──
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


def _norm_symbol(raw: str | None) -> str:
    sym = (raw or "").strip().upper().replace("/", "")
    if not sym:
        return ""
    if not sym.endswith("USDT"):
        sym += "USDT"
    return sym
