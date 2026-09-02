"""Telegram UI for HYPE v3 (interactive platform).

``V3Core`` is a pure, testable application service (no aiogram imports): it
turns chat text / callback payloads into replies. ``V3TelegramTransport`` is a
thin aiogram adapter: authorization, inline keyboards, message editing.

Authorisation: the bot is private. When a real ``user_id`` is provided the
core checks ``SignalConfig.allowed_user_ids`` (``TELEGRAM_ALLOWED_USER_IDS``,
fallback ``TELEGRAM_ADMIN_CHAT_ID``). With no allow-list configured the
transport denies everyone and logs a clear operator warning.

Commands are still supported for power users:
  /help /status /signal BTCUSDT [pro] /scan [pro] /walkforward BTCUSDT [tf]
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from src.core.logging import get_logger
from v3.alerts import render_alert, render_signal_alert
from v3.config import SignalConfig, build_line
from v3.data import FuturesDataService
from v3.engine import FuturesSignalEngine
from v3.observability import metrics
from v3.publisher import sanitize_for_publish
from v3.report import render_signal
from v3.store import SignalLifecycle, SignalStore
from v3.tg import keyboards as kb
from v3.tg import render as rv
from v3.tg.settings import UserSettingsService

LOGGER_NAME = "v3.telegram"
logger = get_logger(LOGGER_NAME)

_CONFIDENCE_PART_LABELS = {
    "quality": "качество сетапа",
    "data": "свежесть и полнота данных",
    "trend": "согласованность таймфреймов",
    "confirm": "объём/стакан/позиции",
    "risk": "риск-профиль",
    "impulse": "ранняя готовность импульса",
}


def _confidence_weights_line() -> str:
    """«качество сетапа 34% · свежесть данных 16% · …» — из реального конфига.

    Строка HELP обязана совпадать с весами, которыми бот считает уверенность
    прямо сейчас (веса настраиваются через ``BOT_CONFIDENCE_WEIGHTS``). Если
    конфиг не собрался (битый env на этапе импорта) — берём дефолт, а не
    падаем: HELP не должен ломать импорт модуля.
    """
    from v3.config import DEFAULT_BOT_CONFIDENCE_WEIGHTS

    try:
        weights = SignalConfig().bot_confidence_weights
    except Exception:  # noqa: BLE001
        weights = dict(DEFAULT_BOT_CONFIDENCE_WEIGHTS)
    return " · ".join(
        f"{_CONFIDENCE_PART_LABELS.get(key, key)} {value * 100:.0f}%"
        for key, value in weights.items()
    )


HELP_TEXT = f"""🤖 **HYPE — futures signal intelligence (только реальные данные)**

Платформа анализирует ТОЛЬКО реальные данные бирж (Bybit → Binance → MEXC):
цены, свечи, стакан, фандинг, OI, ликвидации, L/S ratio, новости. Никаких
демо-данных и «приблизительных» значений: чего нет — показано как «н/д».

**Три метрики — три разные вещи (их часто путают)**

🎯 **Уверенность бота (0–100%)** — насколько независимые анализы бота согласны
между собой. Складывается из шести блоков (вес каждого показан в карточке):
  {_confidence_weights_line()}.
Веса настраиваются через `BOT_CONFIDENCE_WEIGHTS` — строка выше всегда
соответствует текущей конфигурации бота.

⭐ **Оценка сетапа (0–100)** — это качество сетапа, а не вероятность прибыли:
  S 82–100 — отличный
  A 72–81 — хороший
  B 62–71 — средний, нужна дисциплина
  C 50–61 — слабый, обычно не входим
  ниже 55 — жёсткий минимум: вход запрещён (NO TRADE)

📦 **Полнота данных (%)** — сколько реальных источников ответило и насколько
свежими были свечи. Низкая полнота честно режет уверенность.

⚠️ Ни одна из метрик НЕ является вероятностью прибыли. Сетап 90/100 и
уверенность 85% тоже могут закрыться по стопу.

**Разделы**

🔎 **СКАНИРОВАТЬ РЫНОК** — весь рынок USDT-perp: отбор → глубокий анализ
⚡ **НАМЕЧАЕТСЯ ДВИЖЕНИЕ** — монеты до разгона (объём, сжатие, позиция в диапазоне)
🔥/🔻 **ЛУЧШИЕ LONG / SHORT** — списки сетапов по направлению
⭐ **ТОП ВОЗМОЖНОСТИ** — только сильные сетапы (строгий порог)
🔍 **АНАЛИЗ МОНЕТЫ** — полный разбор конкретного тикера
🔔 **АВТО-СИГНАЛЫ** — бот сам следит за рынком и пишет без вашего запроса
📊 **МОЙ РЫНОК** — BTC/ETH, доминация, настроение рынка
⚙️ **НАСТРОЙКИ** — депозит, риск на сделку, режим отчёта
📚 **ПОМОЩЬ** — глоссарий простыми словами

**Команды** (работают наравне с кнопками)

`/signal BTCUSDT` — анализ + LONG/SHORT/NO TRADE
`/signal BTCUSDT pro` — полный факторный разбор
`/scan` — скан вселенной USDT-perp
`/alerts` — статус авто-сигналов и пороги
`/market` — обзор рынка
`/walkforward BTCUSDT [15m]` — walk-forward проверка на истории
`/status` — сохранённые сигналы/последний скан

**Как ловим начало движения:** `EARLY` — база просыпается, `TRIGGERED` —
закрытая свеча подтвердила первый выход, `EXHAUSTED` — движение уже далеко и
его не догоняем. В daemon поиск идёт по всей ликвидной вселенной; явный
`watch` — только по указанным монетам.

**Честный NO TRADE.** Если сетап слабый, бот прямо пишет, что входа нет и
почему. Это не поломка — это риск-менеджмент.

Бот **не торгует**. Это аналитический сигнал, не гарантия результата.

{build_line()}
Если строка сборки не совпадает с репозиторием — запущен старый процесс:
сделайте `git pull` и перезапустите бота."""

WELCOME_TEXT = f"""👋 **Добро пожаловать в HYPE — Crypto Market Intelligence**

Я — аналитический бот по USDT-перпетуалам. Работаю только на реальных данных
бирж (Bybit → Binance → MEXC): цены, свечи 5m–1d, стакан, фандинг, открытые
позиции, ликвидации. Никаких демо-данных: чего нет — показываю как «н/д».

**Чем я полезен**

🔎 Нахожу лучшие сетапы по всему рынку, а не по списку из 10 монет
⚡ Замечаю монеты ДО разгона: объём проснулся, волатильность сжалась
🧮 Считаю вход, стоп, цели и размер позиции под ваш депозит и риск
🚫 Честно говорю NO TRADE, когда чистого сетапа нет
🔔 Сам слежу за рынком и присылаю сигнал без вашего запроса — но только если
   сетап действительно сильный

**Как читать мои цифры (коротко)**

🎯 Уверенность бота — насколько согласны между собой мои независимые анализы
⭐ Оценка сетапа — качество комбинации факторов
📦 Полнота данных — сколько источников ответило
⚠️ Ни одна цифра не является вероятностью прибыли.

**С чего начать**

1️⃣ Нажмите «🔎 СКАНИРОВАТЬ РЫНОК» — я покажу, что сейчас сильное
2️⃣ Или отправьте тикер сообщением, например `SOLUSDT`
3️⃣ В «⚙️ НАСТРОЙКИ» укажите депозит и риск — расчёт позиции станет точным
4️⃣ В «🔔 АВТО-СИГНАЛЫ» включите уведомления, чтобы не следить вручную

❗ Я не торгую и не гарантирую результат. Шорт и плечо — повышенный риск.

{build_line()}
Если строка сборки не совпадает с репозиторием — запущен старый процесс:
сделайте `git pull` и перезапустите бота."""

MENU_TEXT = f"""🧠 **HYPE — CRYPTO MARKET INTELLIGENCE**

Аналитическая платформа USDT-perp: сканер рынка, multi-timeframe, деривативы,
риск-менеджмент и честный NO TRADE.

**Разделы**

🔎 **СКАНИРОВАТЬ РЫНОК** — быстрый скан → глубокий анализ лучших
🔥/🔻 — лучшие **LONG** / **SHORT** сетапы
⭐ **ТОП ВОЗМОЖНОСТИ** — лучшие сетапы независимо от направления
🔍 **АНАЛИЗ МОНЕТЫ** — полный разбор выбранной монеты
🔔 **АВТО-СИГНАЛЫ** — бот сам следит за рынком и пишет без запроса
📊 **МОЙ РЫНОК** — BTC/ETH/глобальный контекст
⚙️ **НАСТРОЙКИ** — режим отчёта, депозит, риск
📚 **ПОМОЩЬ** — простые объяснения терминов

**Как читать отчёты**

🎯 Уверенность бота (%) — согласованность независимых анализов
⭐ Оценка сетапа (0–100) — качество комбинации факторов
📦 Полнота данных (%) — сколько источников ответило
⚠️ Это не вероятность прибыли. Дата данных и свежесть есть в каждом отчёте.

❗ Система не торгует и не гарантирует результат.

{build_line()}
Если строка сборки не совпадает с репозиторием — запущен старый процесс:
сделайте `git pull` и перезапустите бота."""


@dataclass
class BotReply:
    """Ответ бота на callback/команду.

    ``edit=False`` (по умолчанию) — НОВОЕ сообщение: история диалога никогда
    не перезаписывается. ``edit=True`` — только для навигации внутри одного
    результата (пагинация, переключение вида карточки, кнопка «🔄 ОБНОВИТЬ»).
    Удаление сообщений запрещено — транспорт никогда не вызывает delete_message.
    """

    text: str
    keyboard: Any = None
    edit: bool = False


class V3Core:
    def __init__(
        self,
        data: FuturesDataService,
        engine: FuturesSignalEngine,
        store: SignalStore,
        lifecycle: SignalLifecycle,
        cfg: SignalConfig | None = None,
        user_settings: UserSettingsService | None = None,
    ) -> None:
        self.data = data
        self.engine = engine
        self.store = store
        self.lifecycle = lifecycle
        self.cfg = cfg or SignalConfig()
        self.user_settings = user_settings or UserSettingsService(store, self.cfg)
        # set by V3TelegramTransport for diagnostics (pulse / /status)
        self.transport: V3TelegramTransport | None = None
        # set by the daemon/bot entrypoints: фоновый сканер и канал доставки
        # авто-сигналов. Без них раздел «🔔 АВТО-СИГНАЛЫ» честно пишет, что
        # фоновый процесс не подключён, а «Проверить сейчас» делает разовый скан.
        self.watcher: Any = None
        self.on_alerts: Any = None
        self._scanner: Any = None
        self._signals: dict[str, Any] = {}
        self._awaiting_deposit: set[int] = set()

    # ── authorization ────────────────────────────────────────────
    def authorize(self, user_id: int | None) -> bool:
        if user_id is None:
            # Legacy programmatic path (CLI / tests) -- no auth layer active.
            return True
        allowed = self.cfg.allowed_user_ids
        if not allowed:
            logger.warning("telegram: no allow-list configured; access denied for all users")
            metrics.record_auth_denial()
            return False
        if int(user_id) not in allowed:
            metrics.record_auth_denial()
            return False
        return True

    @property
    def access_denied_text(self) -> str:
        return rv.ACCESS_DENIED

    # ── texts (pure) ─────────────────────────────────────────────
    def welcome_text(self, user_id: int | None = None) -> str:
        """Приветствие при /start: что за бот, что умеет, как читать цифры."""
        text = WELCOME_TEXT
        if user_id is not None:
            settings = self.user_settings.get(user_id).to_dict()
            deposit = float(settings.get("deposit_usd", 0) or 0)
            risk = float(settings.get("risk_per_trade_pct", 0) or 0)
            mode = "PRO" if settings.get("mode") == "pro" else "BEGINNER"
            text += (
                "\n\n👤 **Ваш профиль**\n"
                f"• Режим отчёта: {mode}\n"
                f"• Депозит: ${deposit:,.0f} · риск на сделку: {risk:g}%\n"
                "Изменить — в «⚙️ НАСТРОЙКИ»."
            )
        return text

    def menu_text(self) -> str:
        return MENU_TEXT

    # ── авто-сигналы ─────────────────────────────────────────────
    def _watcher_state(self) -> dict[str, Any]:
        """Состояние фонового сканера для раздела «🔔 АВТО-СИГНАЛЫ»."""
        watcher = self.watcher
        cfg = self.cfg

        def _int(key: str) -> int:
            try:
                return int(self.store.get_state(key, "0") or 0)
            except (TypeError, ValueError):
                return 0

        if watcher is None:
            scope = "определяется при запуске daemon (вся ликвидная вселенная)"
            interval = cfg.WATCHER_INTERVAL_SECONDS
            enabled = bool(cfg.ALERTS_ENABLED)
        else:
            scope = (
                "вся ликвидная вселенная USDT-perp"
                if getattr(watcher, "universe_scan", False)
                else f"список монет: {', '.join(getattr(watcher, 'watchlist', []) or [])}"
            )
            interval = int(getattr(watcher, "interval_seconds", cfg.WATCHER_INTERVAL_SECONDS) or cfg.WATCHER_INTERVAL_SECONDS)
            enabled = bool(getattr(watcher, "alerts_enabled", cfg.ALERTS_ENABLED))
        return {
            "enabled": enabled,
            "interval": interval,
            "scope": scope,
            "last_cycle_ms": _int("v3_last_cycle_ms"),
            "sent_total": _int("v3_alerts_sent"),
            "found_total": _int("v3_alerts_found"),
            "last_alert_ms": _int("v3_last_alert_ms"),
            "last_alert_symbol": self.store.get_state("v3_last_alert_symbol", ""),
            "last_suppressed": self.store.get_state("v3_last_suppressed", ""),
            "active_signals": len(self.lifecycle.active()),
        }

    def alerts_text(self) -> str:
        state = self._watcher_state()
        return rv.render_alerts_page(
            self.cfg,
            enabled=state["enabled"],
            interval_seconds=state["interval"],
            last_cycle_ms=state["last_cycle_ms"],
            sent_total=state["sent_total"],
            found_total=state["found_total"],
            last_alert_ms=state["last_alert_ms"],
            last_alert_symbol=state["last_alert_symbol"],
            active_signals=state["active_signals"],
            scope=state["scope"],
            transport_enabled=bool(self.transport is not None and self.transport.enabled),
            last_suppressed=state["last_suppressed"],
        )

    def alerts_toggle_text(self, force: bool | None = None) -> str:
        """Пауза/включение авто-сигналов. Без watcher — только подсказка.

        ``force`` — явное состояние («/alerts on» не должно переключаться на
        паузу при повторной отправке команды).
        """
        watcher = self.watcher
        if watcher is None or not hasattr(watcher, "toggle_alerts"):
            return (
                "🔔 **АВТО-СИГНАЛЫ**\n\n"
                "⚠️ Фоновый сканер не подключён к этому интерфейсу.\n"
                "Авто-сигналы работают в daemon-процессе: `python -m v3 daemon` "
                "(или `make run`). Пороги и статус — ниже.\n\n"
                + rv.render_alerts_page(self.cfg, enabled=self.cfg.ALERTS_ENABLED)
            )
        enabled = (
            watcher.set_alerts_enabled(bool(force))
            if force is not None
            else watcher.toggle_alerts()
        )
        head = (
            "▶️ **Авто-сигналы включены** — бот снова пишет сам, как только найдёт сильный сетап."
            if enabled
            else "⏸ **Авто-сигналы на паузе** — бот молчит. Анализ по вашему запросу работает как обычно."
        )
        return head + "\n\n" + self.alerts_text()

    async def alerts_now_text(self) -> str:
        """«🔎 Проверить сейчас»: один внеплановый цикл поиска сильного сетапа."""
        watcher = self.watcher
        if watcher is not None and hasattr(watcher, "run_cycle"):
            try:
                await watcher.run_cycle(notify=self.on_alerts)
            except Exception as exc:  # noqa: BLE001
                return f"⚠️ Проверка не удалась: {exc}\n\nПопробуйте ещё раз через минуту."
            texts = []
            for item in getattr(watcher, "last_alerts", []) or []:
                text = render_alert(item, self.cfg)
                if text:
                    texts.append(text)
            return rv.render_alerts_found(texts, self.cfg, checked=int(getattr(watcher, "last_checked", 0) or 0))
        # без фонового сканера делаем честный разовый скан вселенной
        from v3.alerts import evaluate_alert

        try:
            tickers = await self.data.tickers()
        except Exception as exc:  # noqa: BLE001
            return rv.render_no_data(
                [f"тикеры недоступны: {exc}"],
                getattr(self.data, "source_diagnostics", lambda: [])(),
            )
        if not tickers:
            return rv.render_no_data(
                ["биржа вернула пустой список тикеров"],
                getattr(self.data, "source_diagnostics", lambda: [])(),
            )
        from v3.scanner import Scanner

        scanner = Scanner(self.engine, self.cfg)
        result = await scanner.run(tickers, limit=self.cfg.SCAN_LIMIT, top=self.cfg.SCAN_TOP)
        self._scanner = scanner
        texts: list[str] = []
        for item in result.analyzed:
            sig, _ = sanitize_for_publish(item["signal"], self.cfg)
            self._signals[sig.symbol] = sig
            self.store.save_signal(sig)
            decision = evaluate_alert(sig, self.cfg)
            if decision.ok:
                texts.append(render_signal_alert(sig, self.cfg))
        return rv.render_alerts_found(texts[: self.cfg.ALERT_MAX_PER_CYCLE], self.cfg, checked=len(result.analyzed))

    def status_text(self) -> str:
        """«🧾 ИСТОРИЯ СИГНАЛОВ»: те же цифры, что в карточках, человеческим языком."""
        rows = self.store.recent_signals(limit=20)
        status_words = {
            "GENERATED": "сформирован",
            "CONFIRMED": "подтверждён",
            "ACTIVE": "активен",
            "TP1_HIT": "цель 1 достигнута",
            "TP2_HIT": "цель 2 достигнута",
            "TP3_HIT": "цель 3 достигнута",
            "CLOSED": "закрыт по целям",
            "STOPPED": "стоп сработал",
            "INVALIDATED": "отменён рынком",
            "EXPIRED": "устарел",
            "NO_TRADE": "нет входа",
        }
        lines = ["🧾 **ИСТОРИЯ СИГНАЛОВ**", ""]
        if not rows:
            lines.append("Пока пусто: бот ещё не сохранил ни одного анализа.")
        for r in rows[:10]:
            emoji = {"LONG": "🟢", "SHORT": "🔻"}.get(str(r.get("direction")), "⛔")
            direction = str(r.get("direction", "?"))
            payload = r.get("payload") or {}
            confidence = (payload.get("features") or {}).get("bot_confidence") or {}
            conf_txt = (
                f" · уверенность {float(confidence['percent']):.0f}%"
                if confidence.get("percent") is not None
                else ""
            )
            tier = str(r.get("tier") or "")
            tier_txt = f" ({tier})" if tier and tier != "NONE" else ""
            state = status_words.get(str(r.get("status")), str(r.get("status")))
            lines.append(
                f"• {r.get('symbol', '?')} {emoji} {direction} · "
                f"оценка {float(r.get('quality') or 0):.0f}/100{tier_txt}{conf_txt} · {state}"
            )
        try:
            last_ms = int(self.store.get_state("last_scan_ms", "0") or 0)
        except ValueError:
            last_ms = 0
        lines += [
            "",
            f"Сохранено сигналов: {len(rows)} · активных под наблюдением: {len(self.lifecycle.active())}",
            f"Последний скан: {rv.utc_time(last_ms)}",
            f"Режим данных: {self.data.mode} (только реальные данные)",
            f"🔔 Авто-сигналов отправлено: {self.store.get_state('v3_alerts_sent', '0')}"
            + (f" · последний: {self.store.get_state('v3_last_alert_symbol', '')}"
               if self.store.get_state("v3_last_alert_symbol", "") else ""),
        ]
        if self.transport is not None:
            lines.append(f"Telegram: {'включён' if self.transport.enabled else 'выключен'}")
            if self.transport.last_error:
                lines.append(f"Ошибка поллинга: {self.transport.last_error}")
        lines += ["", "❗ Аналитика, не гарантия результата."]
        return "\n".join(lines)

    def pulse_text(self, transport: Any = None, watcher: Any = None, mode: str | None = None) -> str:
        """Operator self-diagnostics: data mode, Telegram state, watcher state."""
        tg = transport or self.transport
        data_mode = mode or getattr(self.data, "mode", "unknown")
        token_state = "задан" if self.cfg.TELEGRAM_BOT_TOKEN else "не задан"
        tg_state = "включён" if tg is not None and tg.enabled else "выключен"
        tg_error = (getattr(tg, "last_error", "") or "нет") if tg is not None else "нет"
        allowlist = self.cfg.allowed_user_ids
        last_cycle = self.store.get_state("v3_last_cycle_ms", "нет")
        last_error = self.store.get_state("v3_last_error", "нет") or "нет"
        watchlist = ", ".join(getattr(watcher, "watchlist", []) or []) or "—"
        lines = [
            "🩺 Диагностика HYPE v3",
            "  Политика данных: только реальные данные бирж (MARKET_DATA_MODE=demo удалён)",
            f"  Режим данных: {data_mode}",
            f"  TELEGRAM_BOT_TOKEN: {token_state}",
            f"  Telegram transport: {tg_state}",
            f"  Ошибка Telegram-поллинга: {tg_error}",
            f"  Allow-list (user ids): {allowlist or '— НЕ НАСТРОЕНА (доступ закрыт)'}",
            f"  Активных сигналов: {len(self.lifecycle.active())}",
            f"  Последний цикл watcher: {last_cycle}",
            f"  Интервал watcher: {getattr(watcher, 'interval_seconds', self.cfg.WATCHER_INTERVAL_SECONDS)}с",
            f"  Авто-сигналы: {'включены' if getattr(watcher, 'alerts_enabled', self.cfg.ALERTS_ENABLED) else 'на паузе'}"
            f" (пороги: качество ≥ {self.cfg.ALERT_MIN_QUALITY:.0f}/100, уверенность ≥ {self.cfg.ALERT_MIN_BOT_CONFIDENCE:.0f}%)",
            f"  Отправлено авто-сигналов: {self.store.get_state('v3_alerts_sent', '0')}",
            f"  Последний авто-сигнал: {self.store.get_state('v3_last_alert_symbol', '—')}",
            f"  Последняя ошибка watcher: {last_error}",
            f"  Watchlist: {watchlist}",
            f"  Сохранено сигналов: {len(self.store.recent_signals(limit=10_000))}",
        ]
        diag_fn = getattr(self.data, "source_diagnostics", None)
        if callable(diag_fn):
            rows = diag_fn() or []
            if rows:
                lines.append("  Источники:")
                for row in rows:
                    state = "доступен" if (row.get("available") or row.get("healthy")) else "недоступен"
                    err = f" ({row.get('last_error')})" if row.get("last_error") else ""
                    lines.append(
                        f"    • {row.get('source', '?')}: {state}{err}"
                        f" [попыток {row.get('attempts', '-')}, ошибок подряд {row.get('consecutive_errors', '-')}]"
                    )
        lines.extend(["", "❗ Самодиагностика, не гарантия результата."])
        return "\n".join(lines)

    # ── message routing ──────────────────────────────────────────
    async def handle_message(self, text: str, _chat_id: Any = None, user_id: int | None = None) -> str:
        if not self.authorize(user_id):
            return self.access_denied_text
        text = (text or "").strip()
        if not text:
            return HELP_TEXT
        if user_id in self._awaiting_deposit:
            return await self._deposit_input(user_id, text)
        lower = text.lower()
        if lower in ("/start", "start", "привет", "hello", "hi"):
            return self.welcome_text(user_id)
        if lower in ("menu", "меню", "главная"):
            return self.menu_text()
        if lower in ("/help", "help", "помощь"):
            return HELP_TEXT
        if lower in ("/status", "status", "статус"):
            return self.status_text()
        if lower in ("/alerts", "alerts", "авто-сигналы", "авто сигналы", "уведомления"):
            return self.alerts_text()
        if lower in ("/alerts on", "alerts on", "включи авто-сигналы"):
            return self.alerts_toggle_text(force=True)
        if lower in ("/alerts off", "alerts off", "выключи авто-сигналы"):
            return self.alerts_toggle_text(force=False)
        if lower in ("/scan", "scan", "скан") or lower.startswith("/scan ") or lower.startswith("scan "):
            mode = "pro" if " pro " in f" {lower} " else "beginner"
            return await self.scan_text(mode)
        if lower.startswith("/walkforward") or lower.startswith("walkforward"):
            return await self.walkforward_text(text)
        if lower.startswith("/signal") or lower.startswith("signal"):
            return await self.signal_text(text)
        if lower in ("/market", "market", "мой рынок", "рынок"):
            return await self.market_text()
        if lower.startswith("glossary") or lower.startswith("что это"):
            term = text.split(maxsplit=1)[1].strip() if len(text.split(maxsplit=1)) > 1 else "list"
            return rv.render_glossary(term)
        # bare symbol like BTCUSDT
        if re.fullmatch(r"[A-Za-z0-9]{2,16}", text.strip()):
            return await self.signal_text(f"/signal {text}")
        return HELP_TEXT

    # ── actions ──────────────────────────────────────────────────
    async def signal_text(self, text: str) -> str:
        parts = text.split()
        symbol = ""
        mode = "beginner"
        for p in parts[1:]:
            if p.lower() in ("pro", "beginner", "simple"):
                mode = p.lower()
            elif not p.startswith("/"):
                symbol = p.upper()
        if not symbol:
            return "Формат: `/signal BTCUSDT` или `/signal BTCUSDT pro`"
        try:
            sig = await self.engine.analyze(symbol, refresh=True)
        except Exception as exc:  # noqa: BLE001
            return f"⚠️ Не удалось проанализировать `{symbol}`: {exc}\n\nПроверьте, что символ торгуется на USDT-perp."
        sig, violations = sanitize_for_publish(sig, self.cfg)
        self._signals[symbol.upper()] = sig
        self.store.save_signal(sig)
        if violations:
            metrics.record_error("publish.blocked", f"{symbol}: {violations}")
        if sig.features.get("no_data"):
            return rv.render_no_data(
                sig.no_trade_reasons or sig.risks or ["нет реальных данных по символу"],
                getattr(self.data, "source_diagnostics", lambda: [])(),
            )
        return render_signal(sig, mode)

    async def scan_text(self, mode: str = "beginner") -> str:
        from v3.scanner import Scanner

        try:
            tickers = await self.data.tickers()
        except Exception as exc:  # noqa: BLE001
            return rv.render_no_data(
                [f"тикеры недоступны: {exc}"],
                getattr(self.data, "source_diagnostics", lambda: [])(),
            )
        if not tickers:
            return rv.render_no_data(
                ["биржа вернула пустой список тикеров"],
                getattr(self.data, "source_diagnostics", lambda: [])(),
            )
        scanner = Scanner(self.engine, self.cfg)
        result = await scanner.run(tickers, limit=self.cfg.SCAN_LIMIT, top=self.cfg.SCAN_TOP)
        self._scanner = scanner
        for item in result.analyzed:
            sig, _ = sanitize_for_publish(item["signal"], self.cfg)
            self._signals[sig.symbol] = sig
            self.store.save_signal(sig)
        now = str(int(__import__("time").time() * 1000))
        self.store.set_state("last_scan_ms", now)
        self.store.set_state("v3_last_scan_ms", now)

        setups = scanner.best_setups()
        top = scanner.top_setups()
        longs = scanner.best_setups("LONG")
        shorts = scanner.best_setups("SHORT")
        summary = rv.scan_summary(
            result.scanned_total,
            len(result.candidates),
            len(result.analyzed),
            setups,
            result.mode or self.data.mode,
            result.duration_sec,
            result.ts_ms,
        )
        lines = [
            "🔎 **СКАН РЫНКА**",
            rv.source_stamp(result.mode or self.data.mode, result.ts_ms),
            summary,
            "",
        ]
        # «⚡ НАМЕЧАЕТСЯ ДВИЖЕНИЕ» — ранний отбор до разгона (признак, а не вход).
        # Пустой список → блок не печатается (render_emerging вернёт "").
        emerging = scanner.emerging() if self.cfg.SCAN_EMERGENCE_ENABLED else []
        block = rv.render_emerging(emerging, self.cfg, pro=(mode == "pro"))
        if block:
            lines += [block, ""]
        if not setups:
            hint = rv.empty_list_hint(result.analyzed, len(result.candidates))
            lines.append("😶 Ни один кандидат не прошёл гейт. Это нормально: "
                         "система предпочитает **NO TRADE** слабому сетапу.")
            if hint:
                lines.append(f"Почему: {hint}")
        else:
            lines.append("✅ **НАЙДЕНО СЕТАПОВ**")
            for i, item in enumerate(setups[:5], 1):
                s = item["signal"]
                emoji = "🟢" if s.direction == "LONG" else "🔻"
                lines.append(
                    f"{i}. {emoji} {s.symbol} {s.direction} — {rv.quality_label(s.quality, s.tier, self.cfg)}"
                    f" · {rv.confidence_line(signal=s, cfg=self.cfg)}"
                )
            lines.append("Подробнее: 🔥/🔻/⭐ кнопки ниже.")
        # Сколько найденного дотянуло бы до порога авто-сигнала: пользователь
        # видит, почему бот молчит в чате, даже когда сетапы в списке есть.
        from v3.alerts import evaluate_alert

        worthy = [item["signal"] for item in setups if evaluate_alert(item["signal"], self.cfg).ok]
        if worthy:
            names = ", ".join(s.symbol for s in worthy[:3])
            lines.append(
                f"🔔 До порога авто-сигнала дотянули: {len(worthy)} ({names}) — "
                "такие бот присылает сам"
            )
        else:
            lines.append(
                f"🔔 До порога авто-сигнала не дотянул никто "
                f"(нужны качество ≥ {self.cfg.ALERT_MIN_QUALITY:.0f}/100 и уверенность "
                f"≥ {self.cfg.ALERT_MIN_BOT_CONFIDENCE:.0f}%) — бот молчит, а не спамит"
            )
        lines += [
            "",
            f"🔥 LONG: {len(longs)} | 🔻 SHORT: {len(shorts)} | ⭐ ТОП (строгий): {len(top)}",
            "❗ Аналитика, не гарантия прибыли.",
        ]
        return "\n".join(lines)

    async def market_text(self) -> str:
        overview = await self.data.market_overview()
        return rv.render_market(overview)

    async def coin_text(self, symbol: str, mode: str | None = None) -> str:
        symbol = symbol.upper().replace("/", "").replace("-", "")
        mode = mode or ("beginner" if self.cfg.BEGINNER_MODE_DEFAULT else "pro")
        try:
            sig = await self.engine.analyze(symbol, refresh=True)
        except Exception as exc:  # noqa: BLE001
            return f"⚠️ Не удалось проанализировать `{symbol}`: {exc}\n\nПроверьте символ (напр. BTCUSDT)."
        sig, _ = sanitize_for_publish(sig, self.cfg)
        self._signals[symbol] = sig
        self.store.save_signal(sig)
        if sig.features.get("no_data"):
            return rv.render_no_data(
                sig.no_trade_reasons or sig.risks or ["нет реальных данных по символу"],
                getattr(self.data, "source_diagnostics", lambda: [])(),
            )
        return render_signal(sig, mode)

    async def update_coin(self, symbol: str) -> str:
        return await self.coin_text(symbol, "beginner")

    async def pro_coin(self, symbol: str) -> str:
        sig = self._signals.get(symbol.upper())
        if sig is None:
            return await self.coin_text(symbol, "pro")
        return render_signal(sig, "pro")

    def top_items(self, kind: str) -> list[dict[str, Any]]:
        """Сетапы раздела: «⭐ ТОП» — строгий порог, списки — тир-осознанные."""
        scanner = self._scanner
        if scanner is None:
            return []
        if kind == "top":
            return scanner.top_setups()
        if kind == "longs":
            return scanner.best_setups("LONG")
        if kind == "shorts":
            return scanner.best_setups("SHORT")
        return scanner.best_setups()

    def scan_stats_line(self) -> str:
        if self._scanner is None or self._scanner.last is None:
            return ""
        result = self._scanner.last
        setups = self._scanner.best_setups()
        return rv.scan_summary(
            result.scanned_total,
            len(result.candidates),
            len(result.analyzed),
            setups,
            result.mode or self.data.mode,
            result.duration_sec,
            result.ts_ms,
        )

    def top_text(self, kind: str, page: int = 0) -> str:
        items = self.top_items(kind)
        pages = max(1, (len(items) + kb.PAGE_SIZE - 1) // kb.PAGE_SIZE)
        page = max(0, min(page, pages - 1))
        title = {
            "top": "⭐ **ТОП ВОЗМОЖНОСТИ** (строгий отбор)",
            "longs": "🔥 **ЛУЧШИЕ LONG** (B/C тоже видны)",
            "shorts": "🔻 **ЛУЧШИЕ SHORT** (B/C тоже видны)",
        }.get(kind, "⭐ ТОП")
        hint = ""
        if not items and self._scanner is not None and self._scanner.last is not None:
            hint = rv.empty_list_hint(self._scanner.last.analyzed, len(self._scanner.last.candidates))
        return rv.render_setup_list(
            items, title, page, pages, self.cfg,
            stats_line=self.scan_stats_line(),
            empty_hint=hint,
        )

    def settings_text(self, user_id: int) -> str:
        return rv.render_settings(self.user_settings.get(user_id).to_dict(), self.cfg)

    async def _deposit_input(self, user_id: int, text: str) -> str:
        stripped = text.strip().replace(",", "").replace("$", "")
        try:
            value = float(stripped)
        except ValueError:
            return "⚠️ Введите сумму цифрами, например: `2500`"
        self._awaiting_deposit.discard(user_id)
        self.user_settings.apply(user_id, "deposit_usd", str(value))
        return "✅ Депозит обновлён: $" + f"{value:,.0f}" + "\n\n" + self.settings_text(user_id)

    def glossary_text(self, term: str) -> str:
        return rv.render_glossary(term)

    async def walkforward_text(self, text: str) -> str:
        from v3.walkforward import WalkForwardConfig, walk_forward

        parts = text.split()
        symbol = next((p.upper() for p in parts[1:] if re.fullmatch(r"[A-Z0-9]{2,16}", p.upper())), "")
        tf = next((p for p in parts[1:3] if p in {"1m", "5m", "15m", "30m", "1h", "4h", "1d"}), "15m")
        if not symbol:
            return "Формат: `/walkforward BTCUSDT` или `/walkforward BTCUSDT 1h`"
        try:
            history = await self.data.history(symbol, tf, 5000)
        except Exception as exc:  # noqa: BLE001
            return f"⚠️ Не удалось загрузить историю `{symbol}`: {exc}"
        wf = WalkForwardConfig(
            train_bars=600, test_bars=300, step_bars=300, n_folds=5, warmup_bars=120,
            entry_tf=tf,
            medium_tf=_medium_from(tf),
            macro_tf=_macro_from(tf),
        )
        res = walk_forward(self.engine, symbol, history, self.cfg, wf)
        if res.error:
            return f"⚠️ Walk-forward `{symbol}` `{tf}`: {res.error}"
        agg = res.aggregate
        st = res.stability
        lines = [
            f"🧪 Walk-forward `{symbol}` @ `{tf}`",
            f"  folds: {len(res.folds)} | verdict: {st['verdict']}",
            f"  mean expectancy: {st['fold_expectancy_mean']}R | std: {st['fold_expectancy_std']}R",
            f"  positive folds: {st['positive_folds']}/{st['total_folds']}",
            "",
            "Агрегированно:",
            f"  trades {agg.get('trades', 0)} | win_rate {agg.get('win_rate', 0):.1f}%",
            f"  prof_factor {agg.get('profit_factor', 0):.2f} | expectancy {agg.get('expectancy_r', 0):+.3f}R",
            f"  max consecutive losses {agg.get('max_consecutive_losses', 0)}",
            "",
            "❗ Тест на истории не гарантирует будущих результатов.",
        ]
        return "\n".join(lines)

    # ── callback router ──────────────────────────────────────────
    # Правила истории диалога (задача: сообщения не пропадают):
    #   * независимый запрос (меню, скан, рынок, монета, настройки, помощь) —
    #     ВСЕГДА новое сообщение (edit=False), старое не трогаем;
    #   * навигация внутри одного результата (пагинация ◀️/▶️, переключение
    #     PRO/карточки, «🔄 ОБНОВИТЬ») — edit того же сообщения (edit=True);
    #   * delete_message не вызывается никогда.
    async def handle_callback(self, data: str, user_id: int | None = None) -> BotReply:
        if not self.authorize(user_id):
            return BotReply(self.access_denied_text, edit=False)
        data = (data or "").strip()
        if data == "welcome":
            return BotReply(self.welcome_text(user_id), kb.main_menu(), edit=False)
        if data == "menu":
            return BotReply(self.menu_text(), kb.main_menu(), edit=False)
        if data == "help":
            return BotReply(HELP_TEXT, kb.glossary_menu(), edit=False)
        # ── авто-сигналы: независимый раздел → новое сообщение,
        #    переключение/проверка → правим это же сообщение
        if data == "alerts":
            state = self._watcher_state()
            return BotReply(self.alerts_text(), kb.alerts_menu(state["enabled"]), edit=False)
        if data == "alerts:toggle":
            text = self.alerts_toggle_text()
            enabled = self._watcher_state()["enabled"]
            return BotReply(text, kb.alerts_menu(enabled), edit=True)
        if data == "alerts:now":
            text = await self.alerts_now_text()
            enabled = self._watcher_state()["enabled"]
            return BotReply(text, kb.alerts_menu(enabled), edit=False)
        if data == "scan":
            text = await self.scan_text("beginner")
            k = kb.scan_results(
                bool(self._scanner and self._scanner.best_setups("LONG")),
                bool(self._scanner and self._scanner.best_setups("SHORT")),
                bool(self._scanner and self._scanner.top_setups()),
            )
            return BotReply(text, k, edit=False)
        if data == "market":
            return BotReply(await self.market_text(), kb.scan_action(), edit=False)
        if data.startswith("list:"):
            _, kind, page = data.split(":")
            items = self.top_items(kind)
            pages = max(1, (len(items) + kb.PAGE_SIZE - 1) // kb.PAGE_SIZE)
            text = self.top_text(kind, int(page or 0))
            return BotReply(text, kb.setups_pager(kind, int(page or 0), pages), edit=True)
        if data.startswith("pick:"):
            page = int(data.split(":")[1] or 0)
            symbols = await self._pick_symbols()
            pages = max(1, (len(symbols) + 20 - 1) // 20)
            page = max(0, min(page, pages - 1))
            chunk = symbols[page * 20 : (page + 1) * 20]
            text = (f"🔍 **АНАЛИЗ МОНЕТЫ** — выберите символ (стр. {page + 1}/{pages})\n\n"
                    + "\n".join(f"• `{s}`" for s in chunk)
                    + "\n\nМожно также просто отправить символ сообщением, например `SOLUSDT`.")
            return BotReply(text, kb.coin_list(chunk, page, pages), edit=False)
        if data.startswith("coin:"):
            symbol = data.split(":", 1)[1].upper()
            return BotReply(await self.coin_text(symbol, "beginner"), kb.coin_card(symbol), edit=False)
        if data.startswith("update:"):
            symbol = data.split(":", 1)[1].upper()
            # кнопка «🔄 ОБНОВИТЬ» карточки — правим её же (fallback на новое
            # сообщение в транспорте, если редактирование не удалось)
            return BotReply(await self.coin_text(symbol, "beginner"), kb.coin_card(symbol), edit=True)
        if data.startswith("pro:"):
            symbol = data.split(":", 1)[1].upper()
            return BotReply(await self.pro_coin(symbol), kb.coin_card(symbol), edit=True)
        if data.startswith("glossary:"):
            term = data.split(":", 1)[1]
            if term == "list":
                return BotReply(rv.render_glossary("list"), kb.glossary_menu(), edit=True)
            return BotReply(rv.render_glossary(term), kb.glossary_back(term), edit=True)
        if data == "settings":
            if user_id is None:
                return BotReply("⚙️ Настройки доступны только из Telegram (укажите user_id).", edit=False)
            return BotReply(self.settings_text(user_id), kb.settings_menu(self.user_settings.get(user_id).to_dict()), edit=False)
        if data.startswith("set:mode:"):
            if user_id is None:
                return BotReply("⚙️ Настройки доступны только из Telegram.", edit=False)
            mode = data.split(":", 2)[2]
            self.user_settings.apply(user_id, "mode", mode)
            return BotReply(self.settings_text(user_id), kb.settings_menu(self.user_settings.get(user_id).to_dict()), edit=True)
        if data.startswith("set:deposit:custom"):
            if user_id is None:
                return BotReply("⚙️ Настройки доступны только из Telegram.", edit=False)
            self._awaiting_deposit.add(user_id)
            return BotReply("💰 Введите сумму депозита в USD цифрами, например `2500`:\n\n0 — вернуться назад", kb.deposit_presets(), edit=True)
        if data.startswith("set:deposit:"):
            if user_id is None:
                return BotReply("⚙️ Настройки доступны только из Telegram.", edit=False)
            value = data.split(":", 2)[2]
            self.user_settings.apply(user_id, "deposit_usd", value)
            return BotReply(self.settings_text(user_id), kb.settings_menu(self.user_settings.get(user_id).to_dict()), edit=True)
        if data == "dep_custom":
            if user_id is None:
                return BotReply("⚙️ Настройки доступны только из Telegram.", edit=False)
            self._awaiting_deposit.add(user_id)
            return BotReply("💰 Введите сумму депозита в USD цифрами, например `2500`:",
                            kb.deposit_presets(), edit=True)
        if data == "set:risk":
            if user_id is None:
                return BotReply("⚙️ Настройки доступны только из Telegram.", edit=False)
            return BotReply("⚠️ **Риск на сделку** — выберите пресет:\n\n"
                            "Меньший риск = меньший размер позиции при том же стопе.",
                            kb.risk_presets(), edit=True)
        if data.startswith("set:risk:"):
            if user_id is None:
                return BotReply("⚙️ Настройки доступны только из Telegram.", edit=False)
            value = data.split(":", 2)[2]
            self.user_settings.apply(user_id, "risk_per_trade_pct", value)
            return BotReply(self.settings_text(user_id), kb.settings_menu(self.user_settings.get(user_id).to_dict()), edit=True)
        if data == "back:menu":
            return BotReply(self.menu_text(), kb.main_menu(), edit=True)
        return BotReply(self.menu_text(), kb.main_menu(), edit=False)

    # ── исполнение BotReply транспортом (тестируемо с fake query) ──
    async def dispatch_callback(self, query: Any) -> None:
        """Маршрутизация + «исполнение» ответа.

        * ``reply.edit == False`` — ВСЕГДА ``query.message.answer`` (история
          диалога не перезаписывается);
        * ``reply.edit == True`` — ``edit_text``, при ошибке редактирования
          (кроме "message is not modified") — fallback на новое ``answer``;
        * ``delete_message`` не вызывается никогда.
        """
        uid = getattr(query.from_user, "id", None) if getattr(query, "from_user", None) else None
        if uid is not None and not self.authorize(uid):
            await query.answer("⛔ Нет доступа", show_alert=True)
            return
        try:
            await query.answer()
        except Exception:  # noqa: BLE001
            pass
        reply = await self.handle_callback(query.data or "", uid)

        async def _send_new() -> None:
            for chunks_i, chunk in enumerate(_split(reply.text, 4000)):
                if chunks_i == 0 and reply.keyboard is not None:
                    await query.message.answer(chunk, reply_markup=reply.keyboard, disable_web_page_preview=True)
                else:
                    await query.message.answer(chunk, disable_web_page_preview=True)

        if not reply.edit:
            await _send_new()
            return
        try:
            if reply.keyboard is not None:
                await query.message.edit_text(reply.text[:4000], reply_markup=reply.keyboard,
                                              disable_web_page_preview=True)
            else:
                await query.message.edit_text(reply.text[:4000], disable_web_page_preview=True)
        except Exception as exc:  # noqa: BLE001
            if "message is not modified" in str(exc).lower():
                return
            try:
                await _send_new()
            except Exception:  # noqa: BLE001
                pass

    async def _pick_symbols(self) -> list[str]:
        """Symbols for the coin picker: watchlist + last scan results."""
        out: list[str] = []
        for s in self.cfg.watchlist:
            if s not in out:
                out.append(s)
        if self._scanner is not None and self._scanner.last is not None:
            for c in self._scanner.last.top_by_heat:
                if c.symbol not in out:
                    out.append(c.symbol)
        if len(out) < 12:
            try:
                ticks = await self.data.tickers()
            except Exception:  # noqa: BLE001
                ticks = {}
            ranked = sorted(
                (t for t in ticks.values() if float(getattr(t, "turnover_24h", 0) or 0) > 0),
                key=lambda t: float(t.turnover_24h or 0),
                reverse=True,
            )
            for t in ranked[:20]:
                if t.symbol not in out:
                    out.append(t.symbol)
        return out[:60]


class V3TelegramTransport:
    def __init__(self, core: V3Core, cfg: SignalConfig) -> None:
        self.core = core
        self.cfg = cfg
        self.enabled = bool(cfg.TELEGRAM_BOT_TOKEN)
        self.last_error: str = ""
        self._bot = None
        self._dp = None
        core.transport = self  # expose transport state to /status and pulse

    async def start(self, handle_signals: bool = True) -> None:
        """Run polling (no-op when disabled)."""
        if not self.enabled:
            return
        from aiogram import Bot, Dispatcher
        from aiogram.filters import CommandStart
        from aiogram.types import CallbackQuery, Message

        self._bot = Bot(token=self.cfg.TELEGRAM_BOT_TOKEN)
        self._dp = Dispatcher()

        async def _guard(message: Message) -> bool:
            uid = getattr(message.from_user, "id", None) if message.from_user else None
            if uid is not None and not self.core.authorize(uid):
                await message.answer(self.core.access_denied_text, disable_web_page_preview=True)
                return False
            return True

        @self._dp.message(CommandStart())
        async def _start(message: Message) -> None:  # pragma: no cover
            if not await _guard(message):
                return
            uid = getattr(message.from_user, "id", None) if message.from_user else None
            await message.answer(
                self.core.welcome_text(uid), reply_markup=kb.main_menu(), disable_web_page_preview=True
            )

        @self._dp.message()
        async def _on_message(message: Message) -> None:  # pragma: no cover
            if not await _guard(message):
                return
            uid = getattr(message.from_user, "id", None) if message.from_user else None
            answer = await self.core.handle_message(message.text or "", message.chat.id, uid)
            for chunk in _split(answer, 4000):
                await message.answer(chunk, disable_web_page_preview=True)

        @self._dp.callback_query()
        async def _on_callback(query: CallbackQuery) -> None:  # pragma: no cover
            await self.core.dispatch_callback(query)

        await self._dp.start_polling(self._bot, handle_signals=handle_signals)

    async def stop(self) -> None:
        if self._bot is not None:
            await self._bot.session.close()

    async def notify_text(self, text: str) -> None:
        """Send an auto-signal/event to every configured chat (no-op if off).

        Адресаты: ``ALERT_CHAT_IDS`` (через запятую), иначе
        ``TELEGRAM_ADMIN_CHAT_ID``. Один сбой доставки не должен обрывать
        отправку остальным адресатам — ошибка пишется в ``last_error``.
        """
        if not self.enabled or self._bot is None:
            return
        chat_ids = self.cfg.alert_chat_ids
        if not chat_ids:
            return
        for chat_id in chat_ids:
            try:
                for chunk in _split(text, 4000):
                    await self._bot.send_message(chat_id, chunk, disable_web_page_preview=True)
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"notify to {chat_id}: {type(exc).__name__}: {exc}"
                logger.error("Telegram notify: %s", self.last_error)


def _medium_from(tf: str) -> str:
    return {"1m": "15m", "5m": "15m", "15m": "1h", "30m": "2h", "1h": "4h", "4h": "1d"}.get(tf, "1h")


def _macro_from(tf: str) -> str:
    return {"1m": "4h", "5m": "4h", "15m": "4h", "30m": "4h", "1h": "1d", "4h": "1d"}.get(tf, "4h")


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
