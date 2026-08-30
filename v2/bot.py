"""
Ассистент: разбор пользовательского запроса → отчёт.

Один и тот же «мозг» (AssistantCore) используется из трёх мест:
  * CLI (python -m v2 analyze AURORA),
  * HTTP-API (POST /analyze),
  * Telegram (сообщение «проанализируй 0x…»).

Поэтому логика разбора текста живёт здесь, а транспорт — в cli.py/telegram.py.

Понимаемые запросы:
  «проанализируй 0x1f9840…»  → полный отчёт по адресу;
  «разбери AURORA», «AURORA» → полный отчёт по символу;
  «скан», «/scan»            → трёхуровневый скан рынка;
  «/status»                  → состояние бота, ошибки, метрики;
  «/buy AURORA»              → виртуальная сделка по уровням из отчёта;
  «/help»                    → справка;
  «/engine»                  → какой движок выбран (v2 / v1) для этого чата.
"""

from __future__ import annotations

import re
import time

from v2.ai.openai_client import AIService
from v2.config import V2Config
from v2.core.errors import RiskRejected, TokenNotFound, V2Error
from v2.core.logging import get_logger
from v2.core.monitor import health, monitor
from v2.data.provider import MarketProvider, build_provider
from v2.engine import AnalysisEngine
from v2.executor import Executor
from v2.models import ScanResult
from v2.reporter import render_report, render_scan
from v2.scanner.pipeline import ScannerPipeline

logger = get_logger("bot")

# 0x-адрес (EVM) или минт Solana (32–44 символа base58)
ADDRESS_RE = re.compile(r"\b(0x[a-fA-F0-9]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})\b")
SYMBOL_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9]{1,14})\b")
COMMAND_WORDS = {
    "проанализируй", "проанализировать", "разбери", "разбор", "анализ", "analyze", "analyse",
    "check", "проверь", "что", "как", "стоит", "покупать",
}
STOP_WORDS = {
    "проанализируй", "проанализировать", "разбери", "разбор", "анализ", "analyze", "analyse",
    "check", "проверь", "монету", "токен", "пожалуйста", "pls", "please", "что", "думаешь",
    "стоит", "покупать", "ли", "это", "мне", "the", "token", "coin", "for", "me",
}

HELP_TEXT = """🤖 **HYPE Advisor v2 — что я умею**

📊 `проанализируй 0x1f98…` или `разбери AURORA`
   → полный разбор: безопасность, стакан, теханализ, соцфон, уровни входа.

🔎 `скан` — трёхуровневый скан рынка (объём 5м → скам-фильтр → ончейн).

📈 `/buy AURORA` — открыть виртуальную сделку по уровням из отчёта (paper).

🩺 `/status` — состояние бота: ошибки, провайдеры, метрики.

🔄 `/engine` — какой движок сейчас выбран (v2 / v1).
   Кнопки «🆕 Движок: v2» / «🧮 Движок: v1» переключают анализ для этого чата.

⚙️ Все фильтры настраиваются в .env (SCAN_L1_ENABLED, L2_MAX_TOP10_PCT, …).
"""

ENGINE_V1 = "v1"
ENGINE_V2 = "v2"
DEFAULT_ENGINE = ENGINE_V2

ENGINE_BTN_V2 = "🆕 Движок: v2"
ENGINE_BTN_V1 = "🧮 Движок: v1"

DEX_ONLY_IN_V2 = (
    "DEX/ончейн-анализ есть только в движке v2. "
    "v1 понимает исключительно CEX-символы вроде `SOLUSDT`.\n"
    "Нажмите «🆕 Движок: v2» или пришлите биржевой тикер."
)


class AssistantCore:
    """Сборка всех сервисов + обработка текстовых запросов."""

    def __init__(self, config: V2Config, provider: MarketProvider | None = None) -> None:
        self.config = config
        self.provider = provider or build_provider(config)
        self.http = getattr(self.provider, "http", None)
        self.ai = AIService(config, self.http)
        self.engine = AnalysisEngine(config, self.provider, self.ai)
        self.pipeline = ScannerPipeline(config, self.provider, self.ai, self.engine)
        self.executor = Executor(config, self.http)
        self.last_scan: ScanResult | None = None
        self.messages_handled = 0
        # выбранный движок анализа: chat_id → "v1" | "v2" (по умолчанию v2)
        self._engine_by_chat: dict[int | str, str] = {}
        self._v1_engine = None
        self._v1_source = None

    async def close(self) -> None:
        close = getattr(self.provider, "close", None)
        if close is not None:
            await close()
        if self._v1_source is not None:
            closer = getattr(self._v1_source, "close", None)
            if closer is not None:
                await closer()

    def _chat_key(self, chat_id: int | str | None) -> int | str:
        return 0 if chat_id is None else chat_id

    def get_engine(self, chat_id: int | str | None = None) -> str:
        """Текущий движок для чата. По умолчанию v2."""
        return self._engine_by_chat.get(self._chat_key(chat_id), DEFAULT_ENGINE)

    def set_engine(self, chat_id: int | str | None, engine: str) -> str:
        """Сохранить выбор движка для чата и вернуть подтверждение."""
        choice = (engine or "").strip().lower()
        if choice not in (ENGINE_V1, ENGINE_V2):
            return f"Неизвестный движок: `{engine}`. Доступно: v1, v2."
        self._engine_by_chat[self._chat_key(chat_id)] = choice
        if choice == ENGINE_V1:
            return (
                "🧮 Движок: **v1** (CEX-советник). Пришлите символ вроде `SOLUSDT`.\n"
                "DEX/ончейн-адреса работают только в v2."
            )
        return "🆕 Движок: **v2** (сканер, скам-фильтр, ончейн)."

    def engine_status_text(self, chat_id: int | str | None = None) -> str:
        current = self.get_engine(chat_id)
        label = "🆕 v2" if current == ENGINE_V2 else "🧮 v1"
        return (
            f"Сейчас выбран движок: **{current}** ({label}).\n"
            "Кнопки: «🆕 Движок: v2» / «🧮 Движок: v1»."
        )

    async def handle_callback(self, callback_data: str, chat_id: int | str | None = None) -> str:
        """Обработчик inline-кнопок. Тестируется без Telegram."""
        data = (callback_data or "").strip()
        if data.startswith("engine:"):
            return self.set_engine(chat_id, data.split(":", 1)[1])
        return f"Неизвестный callback: `{data}`"

    async def _ensure_v1_engine(self):
        """Лениво собирает v1 AnalysisEngine через build_source — только при выборе v1."""
        if self._v1_engine is not None:
            return self._v1_engine
        from src.analysis.engine import AnalysisEngine as V1Engine
        from src.config.settings import Settings
        from src.data.collector import build_source

        mode = self.config.DATA_MODE if self.config.DATA_MODE in ("auto", "live", "demo") else "demo"
        settings = Settings(
            _env_file=None,
            MARKET_DATA_MODE=mode,
            DEFAULT_DEPOSIT_USD=self.config.DEFAULT_DEPOSIT_USD,
            RISK_PER_TRADE_PCT=self.config.RISK_PER_TRADE_PCT,
            MAX_LEVERAGE=self.config.MAX_LEVERAGE,
            MIN_RISK_REWARD=self.config.MIN_RISK_REWARD,
            MAX_POSITION_PCT=self.config.MAX_POSITION_PCT,
            TELEGRAM_BOT_TOKEN="",
        )
        source, _mode = build_source(settings)
        self._v1_source = source
        self._v1_engine = V1Engine(source, settings)
        return self._v1_engine

    async def analyze_v1_text(self, query: str) -> str:
        """Отчёт v1-форматтером по CEX-символу (SOLUSDT)."""
        engine = await self._ensure_v1_engine()
        symbol = to_cex_symbol(query)
        try:
            result = await engine.analyze(symbol, refresh=True)
        except Exception as exc:  # noqa: BLE001 — бот обязан ответить текстом
            return (
                f"⚠️ v1 не смог разобрать `{symbol}`: {exc}\n\n"
                "v1 понимает только CEX-символы вроде `SOLUSDT`."
            )
        return format_v1_report(result)

    # ═══════════════════════════════════════════════════════════
    async def handle_message(self, text: str, chat_id: int | str | None = None) -> str:
        """Точка входа для любого транспорта. Всегда возвращает текст."""
        self.messages_handled += 1
        raw = (text or "").strip()
        if not raw:
            return HELP_TEXT
        lowered = raw.lower()

        try:
            if lowered in ("/help", "help", "помощь", "команды"):
                return HELP_TEXT
            if lowered in ("/status", "status", "статус", "состояние"):
                return self.status_text()
            # кнопки reply/инлайн-клавиатуры переключения движка
            if raw in (ENGINE_BTN_V2, ENGINE_BTN_V1):
                return self.set_engine(chat_id, ENGINE_V2 if raw == ENGINE_BTN_V2 else ENGINE_V1)
            if lowered.split()[0] in ("/engine", "engine", "движок"):
                parts = raw.split()
                if len(parts) >= 2 and parts[1].lower() in (ENGINE_V1, ENGINE_V2):
                    return self.set_engine(chat_id, parts[1].lower())
                return self.engine_status_text(chat_id)
            if lowered in ("/scan", "scan", "скан", "сканируй", "поиск монет", "ищи монеты"):
                return await self.run_scan_text()
            if lowered.startswith("/buy"):
                return await self.buy_text(raw[4:].strip())
            if lowered.startswith("/filters"):
                return self.filters_text()

            if self.get_engine(chat_id) == ENGINE_V1:
                if is_onchain_query(raw):
                    return DEX_ONLY_IN_V2
                query = extract_query(raw)
                if not query:
                    return (
                        "Не понял запрос. v1 понимает только CEX-символы вроде `SOLUSDT`.\n\n"
                        "Примеры:\n• `SOLUSDT`\n• `проанализируй SOL`"
                    )
                if is_onchain_query(query):
                    return DEX_ONLY_IN_V2
                return await self.analyze_v1_text(query)

            query = extract_query(raw)
            if not query:
                return (
                    "Не понял запрос. Пришлите адрес токена (0x…) или символ.\n\n"
                    "Примеры:\n• `проанализируй 0x1f9840a19…`\n• `разбери AURORA`\n• `скан`"
                )
            return await self.analyze_text(query)
        except TokenNotFound as exc:
            logger.warning("Токен не найден: %s", exc)
            return (
                f"⛔ **Не нашёл токен** по запросу `{raw}`.\n\n"
                "Проверьте адрес/символ или пришлите ссылку на пул. "
                f"Технически: {exc}"
            )
        except RiskRejected as exc:
            return f"⛔ **Ордер отклонён риск-менеджером**\n\n{exc}"
        except V2Error as exc:
            monitor.record("bot", exc)
            return f"⚠️ **Ошибка данных**: {exc}\n\nПопробуйте ещё раз или запросите `/status`."
        except Exception as exc:  # noqa: BLE001 — бот обязан ответить, а не упасть
            monitor.record("bot", exc, fatal=False)
            logger.exception("Необработанная ошибка в боте")
            return f"⚠️ Внутренняя ошибка: {type(exc).__name__}: {exc}\n\nДетали — в `/status`."

    # ── отдельные команды ────────────────────────────────────────
    async def analyze_text(self, query: str, deposit_usd: float | None = None) -> str:
        started = time.time()
        report = await self.engine.analyze(query, deposit_usd=deposit_usd)
        text = render_report(report, self.config)
        text += f"\n\n_Обработано за {time.time() - started:.1f}с_"
        return text

    async def run_scan_text(self, limit: int = 150, analyze_top: int = 3) -> str:
        self.last_scan = await self.pipeline.run(limit=limit, analyze_top=analyze_top)
        return render_scan(self.last_scan, self.config)

    async def buy_text(self, query: str) -> str:
        if not query:
            return "Укажите монету: `/buy AURORA`"
        report = await self.engine.analyze(query)
        receipt = await self.executor.open_position(report)
        return (
            f"✅ **Виртуальная сделка открыта** ({receipt.mode})\n\n"
            f"• {receipt.side.upper()} {receipt.qty:.6f} {receipt.symbol} @ {receipt.entry:.8g}\n"
            f"• Стоп: {receipt.stop_loss:.8g} | Цель: {receipt.targets[0]:.8g}\n"
            f"• Объём ${receipt.notional_usd:,.2f} | Риск ${receipt.risk_usd:,.2f}\n\n"
            f"Статистика: {self.executor.stats()}"
        )

    def status_text(self) -> str:
        provider_stats = getattr(self.provider, "stats", lambda: {})()
        lines = [
            "🩺 **Состояние бота**",
            "",
            f"• Режим данных: `{self.config.DATA_MODE}`",
            f"• Обработано сообщений: {self.messages_handled}",
            f"• Анализов выполнено: {self.engine.analyses}",
            f"• Провайдер: {provider_stats}",
            f"• AI: {self.ai.stats()}",
            f"• Исполнитель: {self.executor.stats()}",
            "",
            "**Здоровье:**",
        ]
        snapshot = health.snapshot()
        lines.append(f"• аптайм {snapshot['uptime_sec']:.0f}с")
        for key, value in list(snapshot["state"].items())[:12]:
            lines.append(f"• {key}: {value}")
        lines.append("")
        errors = monitor.snapshot()
        lines.append(
            f"**Ошибок:** всего {errors['total_errors']}, за последние "
            f"{self.config.CIRCUIT_COOLDOWN_SECONDS:.0f}с+ — {errors['errors_in_window']}"
        )
        for key, count in errors["by_component"].items():
            lines.append(f"• {key}: {count}")
        return "\n".join(lines)

    def filters_text(self) -> str:
        lines = ["⚙️ **Активные фильтры**", ""]
        lines.append(f"• L1 быстрый сканер: {'вкл' if self.config.SCAN_L1_ENABLED else 'выкл'}")
        lines.append(
            f"  – объём за 5м ≥ ${self.config.L1_MIN_VOLUME_5M_USD:,.0f}, "
            f"транзакций за 5м ≥ {self.config.L1_MIN_TX_5M}"
        )
        lines.append(f"• L2 скам-фильтр: {'вкл' if self.config.SCAN_L2_ENABLED else 'выкл'}")
        lines.append(
            f"  – топ-10 ≤ {self.config.L2_MAX_TOP10_PCT:.0f}%, LP ≥ "
            f"{self.config.L2_MIN_LP_LOCKED_PCT:.0f}% на ≥ {self.config.L2_MIN_LP_LOCK_DAYS} дней, "
            f"mint {'блок' if self.config.L2_BLOCK_IF_MINTABLE else 'разрешён'}, "
            f"blacklist {'блок' if self.config.L2_BLOCK_IF_BLACKLIST else 'разрешён'}"
        )
        lines.append(f"• L3 ончейн: {'вкл' if self.config.SCAN_L3_ENABLED else 'выкл'}")
        lines.append(
            f"  – деплоер старше {self.config.L3_MIN_DEPLOYER_AGE_DAYS} дней, "
            f"не более {self.config.L3_MAX_DEPLOYER_TOKENS} контрактов"
        )
        lines.append(f"• AI-модуль: {'вкл' if self.ai.enabled else 'выкл (работают заглушки)'}")
        lines.append(f"• WebSocket: {'вкл' if self.config.USE_WEBSOCKET else 'выкл'}")
        lines.append(f"• Риск: {self.config.RISK_PER_TRADE_PCT}% на сделку, "
                     f"стоп {self.config.ATR_SL_MULTIPLIER}·ATR, мин. R:R 1:{self.config.MIN_RISK_REWARD:.1f}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
#  РАЗБОР ЗАПРОСА
# ═══════════════════════════════════════════════════════════════
def extract_query(text: str) -> str:
    """
    Достаёт из произвольной фразы адрес или символ.

    Приоритет: адрес (0x… / минт Solana) → символ в кавычках → последнее
    «похожее на тикер» слово, не входящее в список служебных.
    """
    cleaned = text.replace(",", " ").replace("—", " ").strip()
    match = ADDRESS_RE.search(cleaned)
    if match:
        candidate = match.group(1)
        # отбрасываем ложные срабатывания base58 на обычных длинных словах
        if candidate.startswith("0x") or any(ch.isdigit() for ch in candidate):
            return candidate

    quoted = re.search(r"[\"'«]([^\"'»]{2,20})[\"'»]", cleaned)
    if quoted:
        return quoted.group(1).strip().upper()

    words = [w.strip(".,!?();:«»\"'") for w in SYMBOL_RE.findall(cleaned)]
    tokens = [w for w in words if w.lower() not in STOP_WORDS and not w.isdigit()]
    if not tokens:
        return ""
    # «SOLUSDT» → SOL: биржевой суффикс в DEX-поиске только мешает
    best = tokens[-1].upper()
    for suffix in ("USDT", "USDC", "USD"):
        if best.endswith(suffix) and len(best) > len(suffix):
            best = best[: -len(suffix)]
            break
    return best


def is_onchain_query(text: str) -> bool:
    """True, если запрос — EVM-адрес 0x… или mint Solana, а не CEX-тикер."""
    if not text:
        return False
    match = ADDRESS_RE.search(text.strip())
    if not match:
        return False
    candidate = match.group(1)
    if candidate.startswith("0x"):
        return True
    return any(ch.isdigit() for ch in candidate)


def to_cex_symbol(query: str) -> str:
    """Нормализует запрос к CEX-паре (SOL → SOLUSDT)."""
    symbol = (query or "").strip().upper().replace("/", "").replace("-", "")
    if not symbol:
        return ""
    if symbol.endswith("USDT") or symbol.endswith("USDC"):
        return symbol
    return f"{symbol}USDT"


def format_v1_report(result) -> str:
    """Отчёт v1-форматтером (_short_card) без HTML-разметки Telegram."""
    from src.notify.telegram import _short_card

    html = _short_card(result)
    text = (
        html.replace("<b>", "**")
        .replace("</b>", "**")
        .replace("<i>", "_")
        .replace("</i>", "_")
        .replace("<code>", "`")
        .replace("</code>", "`")
        .replace("&amp;", "&")
    )
    return f"🧮 **Движок v1 (CEX)**\n\n{text}"


def kb_engine(current: str = DEFAULT_ENGINE):
    """Inline-клавиатура переключения движка — образец kb_main() из v1."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=ENGINE_BTN_V2, callback_data="engine:v2"),
                InlineKeyboardButton(text=ENGINE_BTN_V1, callback_data="engine:v1"),
            ],
        ]
    )


def kb_engine_reply():
    """Постоянная reply-клавиатура с кнопками переключения движка."""
    from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=ENGINE_BTN_V2), KeyboardButton(text=ENGINE_BTN_V1)]
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


async def handle_engine_callback(
    core: AssistantCore, callback_data: str, chat_id: int | str | None = None
) -> str:
    """Отдельный обработчик callback-кнопок движка. Тестируется без Telegram."""
    return await core.handle_callback(callback_data, chat_id=chat_id)


# ═══════════════════════════════════════════════════════════════
#  TELEGRAM-ТРАНСПОРТ
# ═══════════════════════════════════════════════════════════════
class TelegramTransport:
    """
    Тонкая обёртка над aiogram: принимает сообщение, отдаёт ответ AssistantCore.

    Вся логика — в AssistantCore, поэтому бота можно тестировать без Telegram.
    Если aiogram не установлен или нет токена, ``enabled`` = False, и процесс
    просто не запускает поллинг.
    """

    def __init__(self, config: V2Config, core: AssistantCore) -> None:
        self.config = config
        self.core = core
        self.enabled = bool(config.telegram_enabled)
        self._dp = None
        self._bot = None

    async def start(self) -> None:
        if not self.enabled:
            logger.info("Telegram выключен (нет TELEGRAM_BOT_TOKEN)")
            return
        from aiogram import Bot, Dispatcher, F
        from aiogram.filters import CommandStart
        from aiogram.types import CallbackQuery, Message

        self._bot = Bot(token=self.config.TELEGRAM_BOT_TOKEN)
        self._dp = Dispatcher()

        @self._dp.message(CommandStart())
        async def _start(message: Message) -> None:  # pragma: no cover - транспорт
            chat_id = message.chat.id
            # reply-клавиатура с переключателем держится внизу чата,
            # inline-kb_engine() — кнопки в самом сообщении
            await message.answer(
                "Используй кнопки ниже или в меню.",
                reply_markup=kb_engine_reply(),
            )
            await message.answer(
                HELP_TEXT,
                reply_markup=kb_engine(self.core.get_engine(chat_id)),
                disable_web_page_preview=True,
            )

        @self._dp.callback_query(F.data.startswith("engine:"))
        async def _on_engine(call: CallbackQuery) -> None:  # pragma: no cover - транспорт
            chat_id = call.message.chat.id if call.message else 0
            answer = await handle_engine_callback(self.core, call.data or "", chat_id=chat_id)
            await call.answer()
            kb = kb_engine(self.core.get_engine(chat_id))
            if call.message:
                await call.message.answer(answer, reply_markup=kb, disable_web_page_preview=True)

        @self._dp.message()
        async def _on_message(message: Message) -> None:  # pragma: no cover - транспорт
            text = message.text or ""
            chat_id = message.chat.id
            answer = await self.core.handle_message(text, chat_id=chat_id)
            kb = kb_engine(self.core.get_engine(chat_id))
            # Telegram ограничивает сообщение 4096 символами — режем по разделителям
            for chunk in _split(answer, 4000):
                await message.answer(chunk, disable_web_page_preview=True, reply_markup=kb)

        logger.info("Telegram-поллинг запущен")
        await self._dp.start_polling(self._bot)

    async def stop(self) -> None:
        if self._bot is not None:
            await self._bot.session.close()


def _split(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > limit:
            parts.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        parts.append(current)
    return parts or [text[:limit]]
