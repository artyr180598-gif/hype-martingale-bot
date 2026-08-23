"""
Main Application Entrypoint and Orchestrator.
"""
import asyncio
import sys

import uvicorn

from src.bot.server import TelegramBotRunner
from src.core.logging import get_logger
from src.database.connection import init_db
from src.scanner.market_scanner import MarketScanner

logger = get_logger("main")


async def run_scan_cli() -> None:
    """Run one-shot quantitative market scan."""
    await init_db()
    scanner = MarketScanner()
    logger.info("Running market opportunity scan...")
    setups = await scanner.scan_market()

    print("\n" + "=" * 60)
    print("🔥 TOP QUANTITATIVE FUTURES SETUPS")
    print("=" * 60)
    for i, s in enumerate(setups, 1):
        print(f"{i}. {s.symbol} | Direction: {s.direction.value} | Score: {s.score:.1f}/100 ({s.tier.value})")
        print(f"   Entry: {s.entry_zone} | SL: ${s.stop_loss:,.2f} | TP1: ${s.take_profit_1:,.2f} | R:R 1:{s.risk_reward_ratio:.1f}")
        print(f"   Regime: {s.market_regime} | Leverage: {s.recommended_leverage}x")
        if s.primary_reasons:
            print(f"   Why: {s.primary_reasons[0]}")
        print("-" * 60)


async def run_bot() -> None:
    """Run Telegram Bot service."""
    await init_db()
    runner = TelegramBotRunner()
    await runner.run_polling()


def run_api() -> None:
    """Run FastAPI REST server."""
    uvicorn.run(
        "src.api.app:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )


async def main_async() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"

    if mode == "scan":
        await run_scan_cli()
    elif mode == "bot":
        await run_bot()
    elif mode == "api":
        run_api()
    elif mode == "all":
        await init_db()
        bot_task = asyncio.create_task(run_bot())
        config = uvicorn.Config("src.api.app:app", host="0.0.0.0", port=8000, log_level="info")
        server = uvicorn.Server(config)
        await asyncio.gather(bot_task, server.serve())
    else:
        print(f"Unknown mode: {mode}. Use: scan, bot, api, all")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
