"""CLI entry points for v3 (used by ``python -m v3`` and ``main.py v3``)."""

from __future__ import annotations

import argparse
import asyncio
import time

from v3.backtest import run_backtest as run_v3_backtest
from v3.config import SignalConfig
from v3.data import FuturesDataService
from v3.engine import FuturesSignalEngine
from v3.report import render_signal
from v3.store import SignalLifecycle, SignalStore

_cfg = SignalConfig()


def _engine(data: FuturesDataService | None = None) -> tuple[FuturesDataService, FuturesSignalEngine]:
    d = data or FuturesDataService(cfg=_cfg)
    return d, FuturesSignalEngine(d, _cfg)


async def run_signal(symbol: str, mode: str = "beginner", deposit: float | None = None) -> int:
    data, engine = _engine()
    store = SignalStore(_cfg.db_path)
    lifecycle = SignalLifecycle(store, _cfg.COOLDOWN_SECONDS, _cfg.MAX_ACTIVE_SIGNALS)
    try:
        await data.probe()
        sig = await engine.analyze(symbol.upper(), refresh=True)
        store.save_signal(sig)
        ok, reason = lifecycle.should_emit(sig)
        print(render_signal(sig, mode))
        if not ok:
            print(f"\n[!] Публикация подавлена: {reason}")
        print("\n" + "=" * 60)
        print(f"Режим данных: {data.mode} | demo: {sig.is_demo}")
        return 0
    finally:
        await data.close()
        store.close()


async def run_scan(limit: int | None = None, top: int | None = None, mode: str = "beginner") -> int:
    from v3.scanner import Scanner

    data, engine = _engine()
    store = SignalStore(_cfg.db_path)
    try:
        await data.probe()
        tickers = await data.tickers()
        scanner = Scanner(engine, _cfg)
        print(f"Сканирую все ликвидные USDT-perp при режиме {data.mode}…")
        result = await scanner.run(tickers, limit=limit, top=top)
        top_n = top or _cfg.SCAN_TOP
        print(
            f"Кандидатов: {len(result.candidates)} | "
            f"глубоко проанализировано: {len(result.analyzed)} | "
            f"{result.duration_sec:.1f}с"
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
        print(f"Загружаю историю {symbol.upper()} @ {tf}…")
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
    uvicorn.run("v3.api:app", host=host, port=port, log_level="info")
    return 0


async def run_bot() -> int:
    from v3.telegram import V3Core, V3TelegramTransport

    data, engine = _engine()
    store = SignalStore(_cfg.db_path)
    lifecycle = SignalLifecycle(store, _cfg.COOLDOWN_SECONDS, _cfg.MAX_ACTIVE_SIGNALS)
    core = V3Core(data, engine, store, lifecycle, _cfg)
    transport = V3TelegramTransport(core, _cfg)
    if not transport.enabled:
        print("Telegram выключен: задайте TELEGRAM_BOT_TOKEN / TELEGRAM_TOKEN")
        return 2
    await data.probe()
    await transport.start()
    return 0


async def run_watch(symbols: list[str] | None = None, interval: int | None = None) -> int:
    from v3.watcher import V3Watcher

    data, engine = _engine()
    store = SignalStore(_cfg.db_path)
    lifecycle = SignalLifecycle(store, _cfg.COOLDOWN_SECONDS, _cfg.MAX_ACTIVE_SIGNALS)
    watcher = V3Watcher(data, engine, store, lifecycle, _cfg, symbols=symbols)
    await data.probe()
    print(f"v3 watcher запущен: {', '.join(watcher.watchlist)} (interval {interval or _cfg.SCAN_INTERVAL_SECONDS}с)")
    await watcher.start()
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        await watcher.stop()
    return 0


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HYPE v3 — futures signal intelligence")
    parser.add_argument("command", nargs="?", default="status", help="signal | scan | backtest | walkforward | status | serve | bot | watch")
    parser.add_argument("symbol", nargs="?", default="", help="symbol")
    parser.add_argument("--mode", default="beginner", help="beginner | pro")
    parser.add_argument("--tf", default="15m", help="entry timeframe")
    parser.add_argument("--bars", type=int, default=1000)
    parser.add_argument("--warmup", type=int, default=120)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--deposit", type=float, default=None)
    parser.add_argument("--host", default="0.0.0.0", help="serve host")
    parser.add_argument("--port", type=int, default=8400, help="serve port")
    args = parser.parse_args(argv)

    cmd = args.command.lower()
    if cmd == "signal":
        if not args.symbol:
            print("Укажите символ: python -m v3 signal BTCUSDT")
            return 2
        return asyncio.run(run_signal(args.symbol, args.mode, args.deposit))
    if cmd == "scan":
        return asyncio.run(run_scan(mode=args.mode))
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
    if cmd == "status":
        return asyncio.run(run_status())
    if cmd == "serve":
        return run_serve(args.host, args.port)
    if cmd == "bot":
        return asyncio.run(run_bot())
    if cmd == "watch":
        syms = [s.strip().upper() for s in args.symbol.split(",") if s.strip()] if args.symbol else None
        return asyncio.run(run_watch(syms))
    print("Доступные команды: signal, scan, backtest, walkforward, status, serve, bot, watch")
    return 2
