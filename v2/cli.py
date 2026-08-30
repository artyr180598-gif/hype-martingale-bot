"""
CLI v2.

  python -m v2 scan                    — трёхуровневый скан рынка
  python -m v2 analyze AURORA          — полный отчёт по монете
  python -m v2 analyze 0x1f98…         — то же по адресу контракта
  python -m v2 watch                   — фоновый скан по расписанию
  python -m v2 serve                   — HTTP-API/дашборд
  python -m v2 bot                     — Telegram-бот (нужен токен)
  python -m v2 status                  — состояние и активные фильтры

Общие флаги: --data-mode auto|live|demo, --deposit, --limit, --analyze-top,
--json, --log-level.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from v2.bot import AssistantCore, TelegramTransport
from v2.config import V2Config, load_config
from v2.core.logging import get_logger, setup_logging
from v2.core.monitor import supervise
from v2.reporter import render_report, render_scan

logger = get_logger("cli")


def _print(text: str) -> None:
    print(text)
    sys.stdout.flush()


async def cmd_scan(core: AssistantCore, args) -> int:
    result = await core.pipeline.run(limit=args.limit, analyze_top=args.analyze_top)
    if args.json:
        _print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        _print(render_scan(result, core.config))
    return 0


async def cmd_analyze(core: AssistantCore, args) -> int:
    report = await core.engine.analyze(args.query, deposit_usd=args.deposit)
    if args.json:
        _print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        _print(render_report(report, core.config))
    return 0


async def cmd_status(core: AssistantCore, _args) -> int:
    _print(core.status_text())
    _print("")
    _print(core.filters_text())
    return 0


async def cmd_watch(core: AssistantCore, args) -> int:
    """Фоновый скан по расписанию. Упавший цикл перезапускает супервизор."""
    stop = asyncio.Event()

    async def cycle() -> None:
        while not stop.is_set():
            try:
                result = await core.pipeline.run(limit=args.limit, analyze_top=args.analyze_top)
                _print(render_scan(result, core.config))
                if result.reports:
                    best = result.reports[0]
                    _print("\n" + "=" * 70)
                    _print(render_report(best, core.config))
                    _print("=" * 70)
            except Exception as exc:  # noqa: BLE001
                logger.error("Цикл скана не удался: %s", exc)
            try:
                await asyncio.wait_for(stop.wait(), timeout=core.config.SCAN_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass

    _print(f"Наблюдение запущено: скан раз в {core.config.SCAN_INTERVAL_SECONDS}с. Ctrl+C — выход.")
    await supervise("watch", cycle, stop_event=stop)
    return 0


async def cmd_serve(core: AssistantCore, args) -> int:
    import uvicorn

    from v2.api import create_app

    app = create_app(core.config, core)
    config = uvicorn.Server(
        uvicorn.Config(app, host=core.config.HOST, port=args.port or core.config.PORT, log_level="info")
    )
    _print(f"API запущен на http://{core.config.HOST}:{args.port or core.config.PORT}")
    await config.serve()
    return 0


async def cmd_bot(core: AssistantCore, _args) -> int:
    transport = TelegramTransport(core.config, core)
    if not transport.enabled:
        _print(
            "⛔ TELEGRAM_BOT_TOKEN не задан. Задайте его в .env или используйте CLI:\n"
            "   python -m v2 analyze AURORA"
        )
        return 2
    await transport.start()
    return 0


COMMANDS = {
    "scan": cmd_scan,
    "analyze": cmd_analyze,
    "analyse": cmd_analyze,
    "status": cmd_status,
    "watch": cmd_watch,
    "serve": cmd_serve,
    "bot": cmd_bot,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m v2",
        description="HYPE Advisor v2 — сканер, скам-фильтр и аналитика по запросу",
    )
    parser.add_argument(
        "command", nargs="?", default="scan",
        help="scan | analyze | watch | serve | bot | status",
    )
    parser.add_argument("query", nargs="?", default="", help="адрес (0x…) или символ (AURORA)")
    parser.add_argument("--data-mode", choices=["auto", "live", "demo"], default=None)
    parser.add_argument("--deposit", type=float, default=None, help="депозит в USDT для расчёта позиции")
    parser.add_argument("--limit", type=int, default=150, help="сколько пулов смотреть на уровне 1")
    parser.add_argument("--analyze-top", type=int, default=3, help="сколько находок анализировать полно")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--json", action="store_true", help="вывод в JSON вместо Markdown")
    parser.add_argument("--log-level", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    overrides = {}
    if args.data_mode:
        overrides["DATA_MODE"] = args.data_mode
    if args.deposit:
        overrides["DEFAULT_DEPOSIT_USD"] = args.deposit
    if args.log_level:
        overrides["LOG_LEVEL"] = args.log_level
    config: V2Config = load_config(**overrides)

    setup_logging(config.LOG_LEVEL, as_json=config.LOG_JSON, force=True)

    command = args.command.lower()
    if command not in COMMANDS:
        _print(
            f"Неизвестная команда: {args.command}\n"
            "Доступно: scan, analyze, watch, serve, bot, status"
        )
        return 2
    if command in ("analyze", "analyse") and not args.query:
        _print("Укажите монету: python -m v2 analyze AURORA (или 0x…)")
        return 2

    async def runner() -> int:
        core = AssistantCore(config)
        try:
            return await COMMANDS[command](core, args)
        finally:
            await core.close()

    try:
        return asyncio.run(runner())
    except KeyboardInterrupt:
        _print("\nОстановлено пользователем.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
