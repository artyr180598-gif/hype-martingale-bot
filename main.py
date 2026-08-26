"""
HYPE Advisor — профессиональный крипто-советник.

Режимы запуска:
  python main.py            — всё: API + сканер + наблюдение + Telegram
  python main.py scan       — разовый скан рынка (поиск скрытых монет)
  python main.py analyze SYM — разовый анализ монеты
  python main.py watch      — только фоновое наблюдение
  python main.py api        — только веб-дашборд/API

Бот НЕ торгует: он мониторит, анализирует и советует.
"""

import argparse
import asyncio
import sys
from pathlib import Path

import uvicorn

from src.config.settings import settings
from src.core.logging import get_logger, setup_logging

logger = get_logger("main")


def _ctx():
    from src.core.context import get_context

    ctx = get_context()
    ctx.ensure_services()
    return ctx


async def run_scan() -> int:
    ctx = _ctx()
    report = await ctx.scanner.scan()
    print("\n" + "=" * 66)
    print(f"🔎 СКАН РЫНКА — режим {report.mode.upper()}")
    print("=" * 66)
    print(
        f"Инструментов: {report.total_instruments} | Кандидатов: {report.candidates} | "
        f"Глубокий анализ: {report.analyzed} | Время: {report.duration_sec:.0f}с"
    )
    if report.gems:
        print("\n🔥 НАЙДЕННЫЕ МОНЕТЫ:")
        for i, g in enumerate(report.gems, 1):
            print(
                f"{i:>2}. {g['symbol']:<14} {g['score']:5.1f}/100 ({g['tier']:<2}) "
                f"{g['direction']:<6} 24ч: {g.get('price_24h_pct', 0):+.1f}%  {g.get('reason', '')[:60]}"
            )
    else:
        print("\nПока ничего выдающегося не найдено.")
    print("=" * 66)
    return 0


async def run_analyze(symbol: str) -> int:
    ctx = _ctx()
    res = await ctx.engine.analyze(symbol.upper(), refresh=True)
    d = res.to_dict()
    print("\n" + "=" * 66)
    print(f"АНАЛИЗ {d['symbol']} — {d['direction']} | {d['score']:.0f}/100 ({d['tier']})")
    print(f"Цена {d['price']:.8g} | 24ч {d['price_24h_pct']:+.2f}% | Уверенность {d['confidence']*100:.0f}%")
    print(f"Тренд: {d['structure']['trend']} (ADX {d['structure']['adx']:.0f}) | "
          f"Волатильность: {d['volatility']['state_ru']} | RSI {d['momentum']['rsi']:.0f}")
    if d.get("plan"):
        p = d["plan"]
        print(f"\nПЛАН {p['direction']}:")
        print(f"  Вход:   {p['entry_zone'][0]:.8g} – {p['entry_zone'][1]:.8g}")
        print(f"  Стоп:   {p['stop_loss']:.8g}")
        print(f"  Цели:   {', '.join(f'{t:.8g}' for t in p['targets'])}")
        print(f"  R:R 1:{p['rr']:.1f} | Плечо ≤ {p['leverage']}x | До зоны {p['distance_pct']:.1f}%")
    print("\nПочему:")
    for r in d["reasons"][:6]:
        print("  " + r)
    print("Риски:")
    for r in d["risks"][:4]:
        print("  ⚠ " + r)
    print("=" * 66)
    return 0


async def run_watch_only() -> int:
    ctx = _ctx()
    watcher = ctx.watcher
    await watcher.start()
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        await watcher.stop()
    return 0


async def _bg_tasks(ctx, notify) -> None:
    """Сканер (раз в SCAN_INTERVAL) + наблюдение + новости."""
    while True:
        try:
            await ctx.scanner.scan()
        except Exception as e:  # noqa: BLE001
            logger.warning("Скан не удался: %s", e)
        try:
            news = await ctx.source.get_news(20)
            if news:
                ctx.store.save_news([n.to_dict() for n in news])
        except Exception as e:  # noqa: BLE001
            logger.debug("Новости недоступны: %s", e)
        await asyncio.sleep(settings.SCAN_INTERVAL_SECONDS)


async def run_all() -> int:
    ctx = _ctx()
    ctx.started = True

    from src.notify.telegram import TelegramAdvisorBot

    notify_bot = TelegramAdvisorBot(settings)

    async def _notify(alerts: list[dict], pos_events: list[dict]) -> None:
        await notify_bot.send_alerts(alerts, pos_events)

    # фоновые задачи: наблюдение + сканер
    bg = asyncio.create_task(_bg_tasks(ctx, _notify))
    watcher_task = None
    if ctx.watcher is not None:
        watcher_task = asyncio.create_task(ctx.watcher._loop(_notify))

    # первый цикл наблюдения сразу
    try:
        await ctx.watcher.run_cycle(_notify)
    except Exception as e:  # noqa: BLE001
        logger.warning("Первый цикл наблюдения не удался: %s", e)

    # Telegram + API
    telegram_task = asyncio.create_task(notify_bot.start_polling()) if notify_bot.enabled else None
    config = uvicorn.Config(
        "src.api.app:app", host=settings.HOST, port=settings.PORT, log_level="info", access_log=False
    )
    server = uvicorn.Server(config)
    api_task = asyncio.create_task(server.serve())

    logger.info(
        "🚀 %s v%s запущен | режим данных: %s | порт %d | Telegram: %s",
        settings.APP_NAME, settings.APP_VERSION, ctx.mode, settings.PORT,
        "включён" if notify_bot.enabled else "выключен",
    )
    if ctx.mode != "live":
        logger.warning("⚠️ ДЕМО-режим: советы генерируются на синтетическом рынке и не являются сигналами!")

    try:
        tasks = [api_task, bg]
        if watcher_task is not None:
            tasks.append(watcher_task)
        if telegram_task is not None:
            tasks.append(telegram_task)
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        await notify_bot.stop()
        await ctx.source.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="HYPE Advisor — крипто-советник")
    parser.add_argument("command", nargs="?", default="all", help="all | scan | watch | api | analyze")
    parser.add_argument("symbol", nargs="?", default="", help="символ для analyze (например SOLUSDT)")
    parser.add_argument("--port", type=int, default=None, help="порт API")
    args = parser.parse_args()

    setup_logging(settings.LOG_LEVEL)

    if args.port:
        settings.PORT = args.port

    cmd = args.command.lower()
    if cmd == "scan":
        return asyncio.run(run_scan())
    if cmd == "analyze":
        if not args.symbol:
            print("Укажите символ: python main.py analyze SOLUSDT")
            return 2
        return asyncio.run(run_analyze(args.symbol))
    if cmd == "watch":
        return asyncio.run(run_watch_only())
    if cmd == "api":
        uvicorn.run(
            "src.api.app:app", host=settings.HOST, port=settings.PORT, log_level="info", access_log=False
        )
        return 0
    if cmd == "all":
        return asyncio.run(run_all())
    print(f"Неизвестная команда: {args.command}. Доступно: all, scan, watch, api, analyze")
    return 2


if __name__ == "__main__":
    sys.exit(main())
