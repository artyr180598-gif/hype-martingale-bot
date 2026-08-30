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
  «/help»                    → справка.
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

⚙️ Все фильтры настраиваются в .env (SCAN_L1_ENABLED, L2_MAX_TOP10_PCT, …).
"""


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

    async def close(self) -> None:
        close = getattr(self.provider, "close", None)
        if close is not None:
            await close()

    # ═══════════════════════════════════════════════════════════
    async def handle_message(self, text: str) -> str:
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
            if lowered in ("/scan", "scan", "скан", "сканируй", "поиск монет", "ищи монеты"):
                return await self.run_scan_text()
            if lowered.startswith("/buy"):
                return await self.buy_text(raw[4:].strip())
            if lowered.startswith("/filters"):
                return self.filters_text()

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
        from aiogram import Bot, Dispatcher
        from aiogram.filters import CommandStart
        from aiogram.types import Message

        self._bot = Bot(token=self.config.TELEGRAM_BOT_TOKEN)
        self._dp = Dispatcher()

        @self._dp.message(CommandStart())
        async def _start(message: Message) -> None:  # pragma: no cover - транспорт
            await message.answer(HELP_TEXT)

        @self._dp.message()
        async def _on_message(message: Message) -> None:  # pragma: no cover - транспорт
            text = message.text or ""
            answer = await self.core.handle_message(text)
            # Telegram ограничивает сообщение 4096 символами — режем по разделителям
            for chunk in _split(answer, 4000):
                await message.answer(chunk, disable_web_page_preview=True)

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
