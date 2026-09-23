"""CLI — unified entry point for HYPE ULTIMATE."""

from __future__ import annotations

import argparse
import asyncio
import sys

from loguru import logger

from .config import build_line, load_config, validate_config
from .daemon import run_daemon
from .data.exchanges import ExchangeManager
from .analysis.engine import analyze_bundle
from .scanner.universe import build_universe
from .signals.generator import analysis_to_signal
from .store.db import SignalStore
from .telegram.render import render_signal_card


def cmd_signal(args):
    cfg = load_config()
    async def _run():
        ex = ExchangeManager(cfg)
        try:
            symbol = args.symbol.upper()
            bundle = await ex.get_bundle(symbol, timeframes=cfg.timeframes)
            if not bundle.has_minimum:
                print(f"⚠️ Нет данных по {symbol}: {bundle.errors}")
                return 1
            analysis = analyze_bundle(bundle, cfg)
            sig = analysis_to_signal(analysis, cfg)
            if not sig:
                print(f"⚠️ Не удалось сгенерировать сигнал для {symbol}")
                print(f"Analysis: {analysis.get('status')} {analysis.get('reason')}")
                return 1
            mode = args.mode
            text = render_signal_card(sig, mode=mode, version=cfg.APP_VERSION, release=cfg.APP_RELEASE)
            print(text)
            # Save
            store = SignalStore(cfg)
            store.save_signal(sig)
            return 0
        finally:
            await ex.close()

    return asyncio.run(_run())


def cmd_scan(args):
    cfg = load_config()
    async def _run():
        ex = ExchangeManager(cfg)
        store = SignalStore(cfg)
        try:
            universe = await build_universe(ex, cfg, max_symbols=args.limit)
            print(f"Universe: {len(universe)} symbols")
            signals = []
            for ticker in universe[: args.top + 20]:
                try:
                    bundle = await ex.get_bundle(ticker.symbol, timeframes=cfg.timeframes)
                    if not bundle.has_minimum:
                        continue
                    analysis = analyze_bundle(bundle, cfg)
                    sig = analysis_to_signal(analysis, cfg)
                    if sig and sig.status == "SIGNAL" and sig.quality_score >= cfg.SCAN_LIST_QUALITY_MIN:
                        signals.append(sig)
                        store.save_signal(sig)
                except Exception as e:
                    logger.debug(f"Scan {ticker.symbol} failed: {e}")
            signals.sort(key=lambda s: s.quality_score * 0.6 + s.confidence_pct * 0.4, reverse=True)
            print(f"\n🔎 Found {len(signals)} signals (top {args.top}):\n")
            for i, sig in enumerate(signals[: args.top], 1):
                print(f"{i}. {sig.symbol} {sig.direction} | {sig.quality_grade} {sig.quality_score:.0f} | conf {sig.confidence_pct:.0f}% | RR 1:{sig.risk_reward:.1f} | {sig.early_phase} | entry {sig.entry:.4f} -> TP {sig.take_profits[0]:.4f} SL {sig.stop_loss:.4f} | exp +{sig.expected_move_pct:.1f}%")
            return 0
        finally:
            await ex.close()

    return asyncio.run(_run())


def cmd_market(args):
    cfg = load_config()
    async def _run():
        ex = ExchangeManager(cfg)
        try:
            btc = await ex.get_bundle("BTCUSDT", timeframes=["1h"])
            eth = await ex.get_bundle("ETHUSDT", timeframes=["1h"])
            universe = await build_universe(ex, cfg, max_symbols=50)
            gainers = sorted([t for t in universe if t.change_24h_pct is not None], key=lambda x: x.change_24h_pct, reverse=True)[:10]
            print(f"BTC: ${btc.ticker.last if btc.ticker else 'n/a'}")
            print(f"ETH: ${eth.ticker.last if eth.ticker else 'n/a'}")
            print("\nTop gainers 24h:")
            for g in gainers:
                print(f"  {g.symbol}: {g.change_24h_pct:+.2f}% @ {g.last}")
            return 0
        finally:
            await ex.close()
    return asyncio.run(_run())


def cmd_status(args):
    cfg = load_config()
    errors = validate_config(cfg)
    if errors:
        print("Config errors:")
        for e in errors:
            print(f"  - {e}")
    else:
        print("Config OK")
    print(build_line(cfg.APP_VERSION, cfg.APP_RELEASE))
    print(f"Exchanges: {cfg.exchanges_list}")
    print(f"Primary: {cfg.PRIMARY_EXCHANGE}")
    print(f"Timeframes: {cfg.timeframes}")
    print(f"DB: {cfg.db_path}")
    store = SignalStore(cfg)
    recent = store.get_recent(limit=5)
    print(f"\nRecent signals: {len(recent)}")
    for r in recent:
        print(f"  {r['symbol']} {r['direction']} {r['quality_grade']} {r['quality_score']:.0f} conf {r['confidence_pct']:.0f}%")
    return 0


def cmd_serve(args):
    cfg = load_config()
    from .api.server import create_app
    import uvicorn

    app = create_app(cfg)
    uvicorn.run(app, host=args.host or cfg.HOST, port=args.port or cfg.PORT)


def cmd_daemon(args):
    cfg = load_config()
    asyncio.run(run_daemon(cfg, host=args.host or cfg.HOST, port=args.port or cfg.PORT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hype-ultimate", description="HYPE ULTIMATE — Multi-exchange scanner & signal intelligence")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)

    sub = parser.add_subparsers(dest="command")

    # signal
    p_signal = sub.add_parser("signal", help="Analyze single coin")
    p_signal.add_argument("symbol", nargs="?", default="BTCUSDT")
    p_signal.add_argument("--mode", default="beginner", choices=["beginner", "pro"])

    # scan
    p_scan = sub.add_parser("scan", help="Scan market universe")
    p_scan.add_argument("--limit", type=int, default=250)
    p_scan.add_argument("--top", type=int, default=20)
    p_scan.add_argument("--mode", default="beginner")

    # market
    sub.add_parser("market", help="Market overview")

    # status
    sub.add_parser("status", help="Status & config check")

    # serve
    p_serve = sub.add_parser("serve", help="Run API server only")
    p_serve.add_argument("--host", default=None)
    p_serve.add_argument("--port", type=int, default=None)

    # daemon
    p_daemon = sub.add_parser("daemon", help="Run full daemon: API + watcher + Telegram")
    p_daemon.add_argument("--host", default=None)
    p_daemon.add_argument("--port", type=int, default=None)

    # bot
    sub.add_parser("bot", help="Run Telegram bot only")

    args = parser.parse_args(argv)

    if not args.command or args.command == "daemon":
        return cmd_daemon(args)
    elif args.command == "signal":
        return cmd_signal(args)
    elif args.command == "scan":
        return cmd_scan(args)
    elif args.command == "market":
        return cmd_market(args)
    elif args.command == "status":
        return cmd_status(args)
    elif args.command == "serve":
        return cmd_serve(args)
    elif args.command == "bot":
        cfg = load_config()
        from .telegram.bot import HypeBot
        from .data.exchanges import ExchangeManager
        from .store.db import SignalStore

        async def _run_bot():
            ex = ExchangeManager(cfg)
            store = SignalStore(cfg)
            bot = HypeBot(cfg, ex, store)
            await bot.start()

        asyncio.run(_run_bot())
        return 0
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
