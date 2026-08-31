"""Telegram UI for v3 signals.

Uses a pure ``V3Core`` (testable without Telegram) and a thin aiogram transport.
Commands:
  /help, /status
  /signal BTCUSDT [beginner|pro]
  /scan [beginner|pro]
"""

from __future__ import annotations

import re
from typing import Any

from v3.config import SignalConfig
from v3.data import FuturesDataService
from v3.engine import FuturesSignalEngine
from v3.report import render_signal
from v3.store import SignalLifecycle, SignalStore

LOGGER_NAME = "v3.telegram"

HELP_TEXT = """🤖 **HYPE v3 — futures signal intelligence**

`/signal BTCUSDT` — анализ + LONG/SHORT/NO TRADE
`/signal BTCUSDT pro` — полный факторный разбор
`/scan` — скан вселенной USDT-perp
`/scan pro` — скан + полный разбор проходных идей
`/walkforward BTCUSDT [15m]` — walk-forward проверка на историю
`/status` — сохранённые сигналы/последний скан

Бот **не торгует**. Это аналитический сигнал, не гарантия результата."""


class V3Core:
    def __init__(
        self,
        data: FuturesDataService,
        engine: FuturesSignalEngine,
        store: SignalStore,
        lifecycle: SignalLifecycle,
        cfg: SignalConfig | None = None,
    ) -> None:
        self.data = data
        self.engine = engine
        self.store = store
        self.lifecycle = lifecycle
        self.cfg = cfg or SignalConfig()

    async def handle_message(self, text: str, _chat_id: Any = None) -> str:
        text = (text or "").strip()
        if not text:
            return HELP_TEXT
        lower = text.lower()
        if lower in ("/help", "help", "помощь"):
            return HELP_TEXT
        if lower in ("/status", "status", "статус"):
            return self.status_text()
        if lower in ("/scan", "scan", "скан") or lower.startswith("/scan ") or lower.startswith("scan "):
            mode = "pro" if " pro " in f" {lower} " else "beginner"
            return await self.scan_text(mode)
        if lower.startswith("/walkforward") or lower.startswith("walkforward"):
            return await self.walkforward_text(text)
        if lower.startswith("/signal") or lower.startswith("signal"):
            return await self.signal_text(text)
        # bare symbol like BTCUSDT
        if re.fullmatch(r"[A-Za-z0-9]{2,16}", text.strip()):
            return await self.signal_text(f"/signal {text}")
        return HELP_TEXT

    def status_text(self) -> str:
        rows = self.store.recent_signals(limit=20)
        lines = [f"🧾 Сохранено v3-сигналов: {len(rows)}", ""]
        for r in rows[:10]:
            lines.append(
                f"  {r['symbol']} {r['direction']:<8} q={r['quality']:.1f} "
                f"tier={r['tier']} {r['status']}"
            )
        last = self.store.get_state("last_scan_ms", "0")
        lines.extend(["", f"Последний скан: {last}", "Режим: " + self.data.mode])
        return "\n".join(lines)

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
        self.store.save_signal(sig)
        return render_signal(sig, mode)

    async def scan_text(self, mode: str = "beginner") -> str:
        from v3.scanner import Scanner

        tickers = await self.data.tickers()
        scanner = Scanner(self.engine, self.cfg)
        result = await scanner.run(tickers, limit=self.cfg.SCAN_LIMIT, top=self.cfg.SCAN_TOP)
        for item in result.analyzed:
            self.store.save_signal(item["signal"])
        now = str(int(__import__("time").time() * 1000))
        self.store.set_state("last_scan_ms", now)
        self.store.set_state("v3_last_scan_ms", now)
        lines = [
            f"🔎 Скан v3: {len(result.candidates)} кандидатов, "
            f"{len(result.analyzed)} глубоких за {result.duration_sec:.1f}с",
            "",
        ]
        for item in result.analyzed[:8]:
            c = item["candidate"]
            s = item["signal"]
            if s.direction in ("LONG", "SHORT"):
                if mode.lower() == "pro":
                    lines.append(render_signal(s, "pro"))
                else:
                    lines.append(f"✅ {c['symbol']} {s.direction} q={s.quality:.1f} tier={s.tier}")
            else:
                lines.append(f"⛔ {c['symbol']} NO TRADE (heat {c['heat']:.1f})")
        lines.append("")
        lines.append("❗ Аналитика, не гарантия прибыли.")
        return "\n".join(lines)

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


class V3TelegramTransport:
    def __init__(self, core: V3Core, cfg: SignalConfig) -> None:
        self.core = core
        self.cfg = cfg
        self.enabled = bool(cfg.TELEGRAM_BOT_TOKEN)
        self._bot = None
        self._dp = None

    async def start(self) -> None:
        if not self.enabled:
            return
        from aiogram import Bot, Dispatcher
        from aiogram.filters import CommandStart
        from aiogram.types import Message

        self._bot = Bot(token=self.cfg.TELEGRAM_BOT_TOKEN)
        self._dp = Dispatcher()

        @self._dp.message(CommandStart())
        async def _start(message: Message) -> None:  # pragma: no cover
            await message.answer(HELP_TEXT, disable_web_page_preview=True)

        @self._dp.message()
        async def _on_message(message: Message) -> None:  # pragma: no cover
            answer = await self.core.handle_message(message.text or "", message.chat.id)
            for chunk in _split(answer, 4000):
                await message.answer(chunk, disable_web_page_preview=True)

        await self._dp.start_polling(self._bot)

    async def stop(self) -> None:
        if self._bot is not None:
            await self._bot.session.close()

    async def notify_text(self, text: str) -> None:
        """Send an event to the configured admin chat (no-op if the bot is not running)."""
        if not self.enabled or self._bot is None:
            return
        chat_id = self.cfg.TELEGRAM_ADMIN_CHAT_ID
        if not chat_id:
            return
        for chunk in _split(text, 4000):
            await self._bot.send_message(chat_id, chunk, disable_web_page_preview=True)


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
