"""CLI entry points for v3 (used by ``python -m v3`` and ``main.py v3``).

Default command is ``daemon``: a single process that runs FastAPI + lifecycle
watcher + Telegram bot.  It must stay alive -- it must never silently fall
back to a one-shot ``status`` print.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from typing import Any

from src.core.logging import get_logger, setup_logging
from v3.alerts import render_alert
from v3.backtest import run_backtest as run_v3_backtest
from v3.config import APP_RELEASE_DEFAULT, APP_VERSION_DEFAULT, SignalConfig, build_line
from v3.data import FuturesDataService
from v3.engine import FuturesSignalEngine
from v3.report import render_signal
from v3.store import SignalLifecycle, SignalStore

logger = get_logger("v3.cli")

try:
    _cfg = SignalConfig()
except Exception as exc:  # например MARKET_DATA_MODE=demo — запрещено
    _cfg = None  # type: ignore[assignment]
    _CFG_ERROR: str | None = str(exc)
else:
    _CFG_ERROR = None


def _engine(data: FuturesDataService | None = None) -> tuple[FuturesDataService, FuturesSignalEngine]:
    d = data or FuturesDataService(cfg=_cfg)
    return d, FuturesSignalEngine(d, _cfg)


async def run_signal(symbol: str, mode: str = "beginner", deposit: float | None = None) -> int:
    data, engine = _engine()
    store = SignalStore(_cfg.db_path)
    lifecycle = SignalLifecycle(store, _cfg.COOLDOWN_SECONDS, _cfg.MAX_ACTIVE_SIGNALS)
    try:
        try:
            await data.probe()
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ Нет реальных данных — анализ невозможен: {exc}")
            print("Все реальные источники недоступны. Проверьте сеть; MARKET_DATA_MODE=live|auto (demo удалён).")
            return 1
        sig = await engine.analyze(symbol.upper(), refresh=True)
        store.save_signal(sig)
        ok, reason = lifecycle.should_emit(sig)
        print(render_signal(sig, mode))
        if not ok:
            print(f"\n[!] Публикация подавлена: {reason}")
        print("\n" + "=" * 60)
        print(f"Источник данных: {data.mode} (только реальные данные)")
        return 0
    finally:
        await data.close()
        store.close()


async def run_scan(limit: int | None = None, top: int | None = None, mode: str = "beginner") -> int:
    """Scan the USDT-perp universe and print the ranked/deep analysis."""
    from v3.scanner import Scanner

    data, engine = _engine()
    store = SignalStore(_cfg.db_path)
    try:
        try:
            await data.probe()
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ Нет реальных данных — скан невозможен: {exc}")
            print("Все реальные источники недоступны. Проверьте сеть; MARKET_DATA_MODE=live|auto (demo удалён).")
            return 1
        tickers = await data.tickers()
        if not tickers:
            print("⚠️ Нет реальных данных — скан невозможен: биржа вернула 0 тикеров.")
            return 1
        scanner = Scanner(engine, _cfg)
        print(f"Сканирую все ликвидные USDT-perp при режиме {data.mode}…")
        result = await scanner.run(tickers, limit=limit, top=top)
        top_n = top or _cfg.SCAN_TOP
        from v3.tg import render as _rv

        print(_rv.scan_summary(
            result.scanned_total, len(result.candidates), len(result.analyzed),
            scanner.best_setups(), result.mode or data.mode, result.duration_sec, result.ts_ms,
        ))
        emerging = scanner.emerging()
        if emerging:
            print("\n⚡ НАМЕЧАЕТСЯ ДВИЖЕНИЕ (ранний отбор, до разгона):")
            for item in emerging[:8]:
                c = item["candidate"]
                print(
                    f"  • {c['symbol']}: ignition {c.get('ignition', 0):.0f}/100 "
                    f"hint {c.get('early_direction', 'FLAT')} | {c.get('emergence_note', '')}"
                )
        for item in result.analyzed[: top_n or 10]:
            c = item["candidate"]
            s = item["signal"]
            if s.direction in ("LONG", "SHORT"):
                print("=" * 60)
                print(render_signal(s, mode))
            else:
                print(
                    f"⛔ {c['symbol']}: NO TRADE / WAIT "
                    f"({s.quality:.1f}, heat {c['heat']:.1f})"
                )
        # persist all analysis snapshots for audit
        for item in result.analyzed:
            store.save_signal(item["signal"])
        now = str(int(time.time() * 1000))
        store.set_state("last_scan_ms", now)
        store.set_state("v3_last_scan_ms", now)
        return 0
    finally:
        await data.close()
        store.close()


async def run_backtest(symbol: str, tf: str = "15m", bars: int = 1000, warmup: int = 120) -> int:
    data, engine = _engine()
    try:
        await data.probe()
        print(f"Загружаю РЕАЛЬНУЮ историю {symbol.upper()} @ {tf} ({data.mode})…")
        history = await data.history(symbol, tf, bars)
        res = run_v3_backtest(
            engine, symbol, history,
            entry_tf=tf,
            medium_tf=_medium_from(tf),
            macro_tf=_macro_from(tf),
            warmup=warmup,
            cfg=_cfg,
        )
        print(f"\nБэктест {res.symbol}: бар {res.start_ts}-{res.end_ts}, сигналов {res.signals}, сделок {len(res.trades)}")
        print("Метрики:")
        for k, v in res.metrics.items():
            print(f"  {k}: {v}")
        print("\nДисклеймер: тест на прошлых данных не гарантирует будущих результатов.")
        return 0
    finally:
        await data.close()


def run_serve(host: str = "0.0.0.0", port: int = 8400) -> int:
    import os

    import uvicorn

    port = int(os.environ.get("PORT") or port)
    # log_config=None -> use the root logger configured by setup_logging()
    uvicorn.run("v3.api:app", host=host, port=port, log_level="info", log_config=None)
    return 0


def _print_startup_report(
    data: FuturesDataService,
    mode: str,
    transport: Any,
    watcher: Any,
    host: str | None,
    port: int,
) -> None:
    """Operator-facing startup banner (deliberately NOT a `status` report)."""
    print("-" * 64)
    # Версия в баннере: по ней видно, какой процесс реально запущен (старый или
    # после обновления). _cfg бывает None, если конфиг не прошёл валидацию.
    version = getattr(_cfg, "APP_VERSION", None) or APP_VERSION_DEFAULT
    release = getattr(_cfg, "APP_RELEASE", None) or APP_RELEASE_DEFAULT
    print(f"HYPE v3 (версия {version}) — Futures Signal Intelligence (единый движок)")
    print(build_line(version, release))
    print(f"Режим данных: {mode}")
    if transport.enabled:
        admin = _cfg.TELEGRAM_ADMIN_CHAT_ID or "не задан"
        allowed = _cfg.allowed_user_ids
        auth = f"allow-list: {allowed}" if allowed else "⚠️ ALLOW-LIST ПУСТА — ДОСТУП БУДЕТ ЗАКРЫТ"
        print(f"Telegram: включён (admin chat: {admin}, {auth})")
    else:
        print("Telegram: выключен — задайте TELEGRAM_BOT_TOKEN (алиас TELEGRAM_TOKEN) в .env")
    scan_scope = "вся ликвидная вселенная (early impulse)" if getattr(watcher, "universe_scan", False) else f"watchlist: {len(watcher.watchlist)} символов"
    interval = _cfg.WATCHER_INTERVAL_SECONDS
    print(f"Watcher: запущен, интервал {interval}с (~{max(1, round(interval / 60))} мин), поиск: {scan_scope}")
    alerts_on = getattr(watcher, "alerts_enabled", _cfg.ALERTS_ENABLED)
    chats = _cfg.alert_chat_ids or ["не задан (TELEGRAM_ADMIN_CHAT_ID)"]
    print(
        "Авто-сигналы: "
        + ("включены" if alerts_on else "на паузе")
        + f" · пороги: качество ≥ {_cfg.ALERT_MIN_QUALITY:.0f}/100, "
        f"уверенность ≥ {_cfg.ALERT_MIN_BOT_CONFIDENCE:.0f}%, "
        f"полнота данных ≥ {_cfg.ALERT_MIN_DATA_CONFIDENCE * 100:.0f}%, "
        f"риск ≤ {_cfg.ALERT_MAX_RISK_SCORE}/10, R:R ≥ 1:{_cfg.ALERT_MIN_RR:.1f}"
    )
    print(f"Доставка авто-сигналов: {', '.join(chats)}")
    if host is not None:
        print(f"API: http://{host}:{port} (Uvicorn running)")
    print("-" * 64)


async def safe_telegram(
    transport: Any,
    *,
    handle_signals: bool = True,
    retries: int = 10,
    retry_delay: float = 5.0,
    max_delay: float = 60.0,
) -> None:
    """Run Telegram polling so it can never kill the daemon.

    Wraps ``transport.start()`` in try/except: every aiogram/start_polling
    failure is logged (never swallowed), remembered in ``transport.last_error``
    (visible via ``pulse`` / ``/status``) and retried with exponential backoff.
    After ``retries`` failed attempts the polling task gives up quietly -- the
    API and the watcher keep running.

    ``handle_signals=False`` keeps uvicorn in charge of SIGTERM/SIGINT so the
    daemon can shut down gracefully (aiogram would otherwise replace uvicorn's
    handlers).
    """

    delay = float(retry_delay)
    attempt = 0
    while True:
        try:
            await transport.start(handle_signals=handle_signals)
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            transport.last_error = f"{type(exc).__name__}: {exc}"
            logger.error("Telegram polling: ошибка — %s", transport.last_error)
            attempt += 1
            if attempt >= retries:
                logger.error(
                    "Telegram polling: %d попыток не удались, поллинг остановлен; "
                    "daemon продолжает работу (API + watcher). Последняя ошибка: %s",
                    attempt,
                    transport.last_error,
                )
                return
            logger.info("Telegram polling: повтор через %.0fс (попытка %d/%d)", delay, attempt + 1, retries)
            await asyncio.sleep(delay)
            delay = min(delay * 2, float(max_delay))


async def run_daemon(host: str = "0.0.0.0", port: int = 8400) -> int:
    """One-process v3 daemon: FastAPI + lifecycle watcher + Telegram bot.

    `python -m v3` (no command) lands here.  The process must stay alive:
    it only exits on SIGINT/SIGTERM.
    """
    import os

    import uvicorn

    from v3.telegram import V3Core, V3TelegramTransport
    from v3.watcher import V3Watcher

    port = int(os.environ.get("PORT") or port)
    data, engine = _engine()
    store = SignalStore(_cfg.db_path)
    lifecycle = SignalLifecycle(store, _cfg.COOLDOWN_SECONDS, _cfg.MAX_ACTIVE_SIGNALS)
    core = V3Core(data, engine, store, lifecycle, _cfg)
    transport = V3TelegramTransport(core, _cfg)
    watcher = V3Watcher(data, engine, store, lifecycle, _cfg)

    try:
        mode = await data.probe()
    except Exception as exc:  # noqa: BLE001
        mode = f"error: {exc}"
        logger.error("Не удалось определить источник данных: %s", exc)

    async def notify(items: list[Any]) -> None:
        for item in items:
            text = render_alert(item, _cfg)
            if text:
                await transport.notify_text(text)

    # UI должен видеть состояние watcher'а (раздел «🔔 АВТО-СИГНАЛЫ») и уметь
    # запустить внеплановую проверку по кнопке «🔎 Проверить сейчас».
    core.watcher = watcher
    core.on_alerts = notify

    await watcher.start(notify=notify, interval=_cfg.WATCHER_INTERVAL_SECONDS)
    _print_startup_report(data, mode, transport, watcher, host, port)

    tasks: list[asyncio.Task[Any]] = [
        asyncio.create_task(
            uvicorn.Server(
                uvicorn.Config("v3.api:app", host=host, port=port, log_level="info", log_config=None)
            ).serve(),
            name="v3.api",
        )
    ]
    if transport.enabled:
        tasks.append(asyncio.create_task(safe_telegram(transport, handle_signals=False), name="v3.telegram"))
    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        # Cancel anything still running (e.g. a polling retry loop) so the
        # process always exits after uvicorn's graceful shutdown.
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await watcher.stop()
        await transport.stop()
        await data.close()
        store.close()
    return 0


async def run_bot() -> int:
    from v3.telegram import V3Core, V3TelegramTransport
    from v3.watcher import V3Watcher

    data, engine = _engine()
    store = SignalStore(_cfg.db_path)
    lifecycle = SignalLifecycle(store, _cfg.COOLDOWN_SECONDS, _cfg.MAX_ACTIVE_SIGNALS)
    core = V3Core(data, engine, store, lifecycle, _cfg)
    transport = V3TelegramTransport(core, _cfg)
    if not transport.enabled:
        print("Telegram выключен: задайте TELEGRAM_BOT_TOKEN / TELEGRAM_TOKEN")
        return 2
    watcher = V3Watcher(data, engine, store, lifecycle, _cfg)

    try:
        mode = await data.probe()
    except Exception as exc:  # noqa: BLE001
        mode = f"error: {exc}"
        logger.error("Не удалось определить источник данных: %s", exc)

    async def notify(items: list[Any]) -> None:
        for item in items:
            text = render_alert(item, _cfg)
            if text:
                await transport.notify_text(text)

    core.watcher = watcher
    core.on_alerts = notify

    await watcher.start(notify=notify, interval=_cfg.WATCHER_INTERVAL_SECONDS)
    _print_startup_report(data, mode, transport, watcher, None, 0)
    polling = asyncio.create_task(safe_telegram(transport), name="v3.telegram")
    try:
        # aiogram handles SIGTERM/SIGINT (stops polling) and returns here.
        await polling
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await watcher.stop()
        await transport.stop()
    return 0


async def run_pulse() -> int:
    """One-shot operator self-diagnostics (does not start the daemon)."""
    from v3.telegram import V3Core, V3TelegramTransport
    from v3.watcher import V3Watcher

    data, engine = _engine()
    store = SignalStore(_cfg.db_path)
    lifecycle = SignalLifecycle(store, _cfg.COOLDOWN_SECONDS, _cfg.MAX_ACTIVE_SIGNALS)
    core = V3Core(data, engine, store, lifecycle, _cfg)
    transport = V3TelegramTransport(core, _cfg)
    watcher = V3Watcher(data, engine, store, lifecycle, _cfg)
    try:
        mode = await asyncio.wait_for(data.probe(), timeout=20)
    except asyncio.TimeoutError:
        mode = "unknown (таймаут probe)"
    except Exception as exc:  # noqa: BLE001
        mode = f"error: {exc}"
    print(core.pulse_text(transport=transport, watcher=watcher, mode=mode))
    rows = data.source_diagnostics()
    if rows:
        print("")
        print("📡 Источники данных (только реальные):")
        for row in rows:
            state = "доступен" if (row.get("available") or row.get("healthy")) else "недоступен"
            note = row.get("last_error") or ""
            print(f"  • {row.get('source', '?')}: {state}"
                  f" | попыток {row.get('attempts', '-')}, ошибок подряд {row.get('consecutive_errors', '-')}"
                  + (f" | последняя ошибка: {note}" if note else ""))
    await data.close()
    store.close()
    return 0


async def run_watch(symbols: list[str] | None = None, interval: int | None = None) -> int:
    from v3.alerts import render_alert as _render_alert
    from v3.watcher import V3Watcher

    data, engine = _engine()
    store = SignalStore(_cfg.db_path)
    lifecycle = SignalLifecycle(store, _cfg.COOLDOWN_SECONDS, _cfg.MAX_ACTIVE_SIGNALS)
    watcher = V3Watcher(data, engine, store, lifecycle, _cfg, symbols=symbols)
    await data.probe()
    every = interval or _cfg.WATCHER_INTERVAL_SECONDS
    print(
        f"v3 watcher запущен: {', '.join(watcher.watchlist)} (interval {every}с, "
        f"авто-сигналы: {'включены' if watcher.alerts_enabled else 'на паузе'})"
    )

    async def _console_notify(items: list[Any]) -> None:
        """Без Telegram-транспорта авто-сигналы всё равно видно — в консоли."""
        for item in items:
            text = _render_alert(item, _cfg)
            if text:
                print("\n" + "=" * 64)
                print(text)

    await watcher.start(notify=_console_notify, interval=every)
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        await watcher.stop()
    return 0


async def run_market() -> int:
    """Print the market-wide overview (same data as Telegram «Мой рынок»)."""
    data, _ = _engine()
    try:
        try:
            await data.probe()
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ Нет реальных данных — обзор рынка невозможен: {exc}")
            print("Все реальные источники недоступны. Проверьте сеть; MARKET_DATA_MODE=live|auto (demo удалён).")
            return 1
        overview = await data.market_overview()
        from v3.tg.render import render_market

        print(render_market(overview))
        return 0
    finally:
        await data.close()


async def run_status() -> int:
    from v3.observability import metrics

    store = SignalStore(_cfg.db_path)
    try:
        rec = store.recent_signals(limit=20)
        print(f"Сохранено сигналов v3: {len(rec)} (последние 20 показаны)")
        for r in rec[:10]:
            print(f"  {r['symbol']} {r['direction']:<8} q={r['quality']:.1f} tier={r['tier']} {r['status']}")
        last = store.get_state("last_scan_ms", "0")
        print(f"Последний скан: {last}")
        snap = metrics.snapshot(
            db_ok=True,
            signals_saved=len(rec),
            outcomes=len(store.outcomes()),
            active_signals=0,
        )
        print(f"Health: mode={snap.mode} analyses={snap.analyses} scans={snap.scan_results} "
              f"avg_latency_ms={snap.latency_avg_ms} last_error={snap.last_error!r}")
        return 0
    finally:
        store.close()


async def run_calibrate(symbols: list[str], tf: str = "15m", bars: int = 2000, warmup: int = 120) -> int:
    from v3.calibrate import calibrate

    data, engine = _engine()
    try:
        await data.probe()
        print(f"Калибровка порогов на выборке: {', '.join(symbols)} @ {tf}, {bars} бар…")
        res = await calibrate(engine, symbols, tf=tf, bars=bars, warmup=warmup, cfg=_cfg)
        print(f"Режим: {res.mode} | символов: {len(res.rows)} | за {res.duration_sec:.1f}с")
        for r in res.rows:
            if r.error:
                print(f"  ⚠ {r.symbol}: {r.error}")
            else:
                print(f"  {r.symbol:<12} сигн {r.signals:>4} сделок {r.trades:>3} "
                      f"win {r.win_rate:>5.1f}% exp {r.expectancy_r:+.3f}R "
                      f"q {r.avg_quality:>5.1f} conf {r.avg_confidence:.2f} loss_seq {r.max_consecutive_losses}")
        print("\nАгрегат:", res.aggregate)
        print("\nРекомендации (не автоматически!):")
        for s in res.suggestions:
            print(f"  • {s}")
        print("\n❗ Калибровка по прошлым данным не гарантирует будущих результатов.")
        return 0
    finally:
        await data.close()


async def run_walkforward(symbol: str, tf: str = "15m", bars: int = 5000, folds: int = 5) -> int:
    from v3.walkforward import WalkForwardConfig, walk_forward

    data, engine = _engine()
    try:
        await data.probe()
        print(f"Walk-forward {symbol.upper()} @ {tf}, {bars} bars, {folds} folds…")
        history = await data.history(symbol, tf, bars)
        wf = WalkForwardConfig(
            train_bars=min(600, bars // 3), test_bars=min(300, bars // 6),
            step_bars=min(300, bars // 6), n_folds=folds, warmup_bars=120,
            entry_tf=tf,
            medium_tf={"1m": "15m", "5m": "15m", "15m": "1h", "30m": "2h", "1h": "4h", "4h": "1d"}.get(tf, "1h"),
            macro_tf={"1m": "4h", "5m": "4h", "15m": "4h", "30m": "4h", "1h": "1d", "4h": "1d"}.get(tf, "4h"),
        )
        res = walk_forward(engine, symbol, history, _cfg, wf)
        print(f"Folds: {len(res.folds)}, aggregate: {res.aggregate}")
        print(f"Stability: {res.stability}")
        if res.error:
            print("Error:", res.error)
        return 0
    finally:
        await data.close()


def _medium_from(tf: str) -> str:
    return {"1m": "15m", "5m": "15m", "15m": "1h", "30m": "2h", "1h": "4h", "4h": "1d"}.get(tf, "1h")


def _macro_from(tf: str) -> str:
    return {"1m": "4h", "5m": "4h", "15m": "4h", "30m": "4h", "1h": "1d", "4h": "1d"}.get(tf, "4h")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="HYPE v3 — futures signal intelligence")
    parser.add_argument(
        "command",
        nargs="?",
        default="daemon",
        help="signal | scan | backtest | walkforward | calibrate | status | pulse | serve | daemon | bot | watch | replay | record (default: daemon)",
    )
    parser.add_argument("symbol", nargs="?", default="", help="symbol (для replay — путь к файлу снапшота)")
    parser.add_argument("--mode", default="beginner", help="beginner | pro")
    parser.add_argument("--tf", default="15m", help="entry timeframe")
    parser.add_argument("--bars", type=int, default=1000)
    parser.add_argument("--warmup", type=int, default=120)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--deposit", type=float, default=None)
    parser.add_argument("--limit", type=int, default=None, help="scan candidate limit")
    parser.add_argument("--top", type=int, default=None, help="deep-analysis top N")
    parser.add_argument("--host", default="0.0.0.0", help="serve host")
    parser.add_argument("--port", type=int, default=8400, help="serve port")
    parser.add_argument("--out", default="", help="record: куда сохранить снапшот (default: data/replay/<SYMBOL>.json)")
    parser.add_argument("--walk", type=int, default=0, help="replay: сколько точек прохода по истории снять")
    parser.add_argument("--step", type=int, default=1, help="replay: шаг прохода в свечах входного ТФ")
    parser.add_argument("--json", action="store_true", help="replay: напечатать результат JSON-ом")
    return parser


def main(argv: list[str] | None = None) -> int:
    from v3.config import validate_config

    if _cfg is None:
        # ошибка валидации конфигурации при старте (например MARKET_DATA_MODE=demo)
        print(f"❌ Ошибка конфигурации: {_CFG_ERROR}")
        print("Исправьте переменные окружения и перезапустите. Платформа работает только на реальных данных.")
        return 2

    setup_logging(_cfg.LOG_LEVEL)
    parser = build_parser()
    args = parser.parse_args(argv)

    config_errors = validate_config(_cfg)
    fatal = [e for e in config_errors if "MARKET_DATA_MODE" in e]
    if fatal:
        for err in fatal:
            print(f"❌ Ошибка конфигурации: {err}")
        return 2
    for err in config_errors:
        logger.warning("config: %s", err)

    cmd = args.command.lower()
    if cmd == "signal":
        if not args.symbol:
            print("Укажите символ: python -m v3 signal BTCUSDT")
            return 2
        return asyncio.run(run_signal(args.symbol, args.mode, args.deposit))
    if cmd == "scan":
        return asyncio.run(run_scan(limit=args.limit, top=args.top, mode=args.mode))
    if cmd == "backtest":
        if not args.symbol:
            print("Укажите символ: python -m v3 backtest BTCUSDT --tf 15m")
            return 2
        return asyncio.run(run_backtest(args.symbol, args.tf, args.bars, args.warmup))
    if cmd == "walkforward":
        if not args.symbol:
            print("Укажите символ: python -m v3 walkforward BTCUSDT --tf 15m --bars 5000 --folds 5")
            return 2
        return asyncio.run(run_walkforward(args.symbol, args.tf, args.bars, args.folds))
    if cmd == "calibrate":
        syms = [s.strip().upper() for s in args.symbol.split(",") if s.strip()]
        if not syms:
            syms = [s for s in _cfg.WATCHLIST_SYMBOLS.split(",") if s.strip()][:3]
        return asyncio.run(run_calibrate(syms, args.tf, args.bars, args.warmup))
    if cmd == "status":
        return asyncio.run(run_status())
    if cmd == "market":
        return asyncio.run(run_market())
    if cmd == "pulse":
        return asyncio.run(run_pulse())
    if cmd == "serve":
        return run_serve(args.host, args.port)
    if cmd == "daemon":
        return asyncio.run(run_daemon(args.host, args.port))
    if cmd == "bot":
        return asyncio.run(run_bot())
    if cmd == "watch":
        syms = [s.strip().upper() for s in args.symbol.split(",") if s.strip()] if args.symbol else None
        return asyncio.run(run_watch(syms))
    if cmd == "replay":
        if not args.symbol:
            print("Укажите файл снапшота: python -m v3 replay v3/tests/fixtures/okx_btcusdt_swap_capture.json")
            return 2
        from v3.replay import run_replay

        return run_replay(args.symbol, args.mode, args.walk, args.step, args.json, _cfg)
    if cmd == "record":
        if not args.symbol:
            print("Укажите символ: python -m v3 record BTCUSDT --out data/replay/btcusdt.json")
            return 2
        from v3.replay import record_symbol

        out = args.out or f"data/replay/{args.symbol.upper()}.json"
        return asyncio.run(record_symbol(args.symbol, out, _cfg))
    print("Доступные команды: signal, scan, market, backtest, walkforward, calibrate, status, pulse, serve, daemon, bot, watch, replay, record")
    return 2
