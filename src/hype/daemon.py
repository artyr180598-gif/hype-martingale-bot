"""Daemon — API + watcher + Telegram in one process (like v3 daemon but ultimate)."""

from __future__ import annotations

import asyncio
import signal
from datetime import datetime, timezone

from loguru import logger

from .analysis.engine import analyze_bundle
from .config import Settings, build_line
from .data.exchanges import ExchangeManager
from .scanner.universe import build_universe
from .signals.generator import analysis_to_signal
from .store.db import SignalStore
from .telegram.bot import HypeBot
from .telegram.render import render_signal_card


async def watcher_loop(cfg: Settings, ex_manager: ExchangeManager, store: SignalStore, bot: HypeBot | None):
    """Background scanner that auto-sends signals."""
    logger.info(f"Watcher started — interval {cfg.WATCHER_INTERVAL_SECONDS}s")
    consecutive_empty = 0

    while True:
        try:
            if bot and bot.alerts_paused:
                logger.info("Watcher paused via Telegram")
                await asyncio.sleep(cfg.WATCHER_INTERVAL_SECONDS)
                continue

            if not cfg.ALERTS_ENABLED:
                await asyncio.sleep(cfg.WATCHER_INTERVAL_SECONDS)
                continue

            logger.info("Watcher cycle start — building universe...")
            universe = await build_universe(ex_manager, cfg, max_symbols=cfg.SCAN_LIMIT)

            if not universe:
                logger.warning("Watcher: empty universe")
                consecutive_empty += 1
                await asyncio.sleep(cfg.WATCHER_INTERVAL_SECONDS)
                continue

            # Stage1: take top pool for emergence
            candidates = universe[: cfg.SCAN_EMERGENCE_POOL]

            signals_found = []

            for ticker in candidates[: cfg.SCAN_TOP + 10]:
                try:
                    bundle = await ex_manager.get_bundle(ticker.symbol, timeframes=cfg.timeframes)
                    if not bundle.has_minimum:
                        continue
                    analysis = analyze_bundle(bundle, cfg)
                    sig = analysis_to_signal(analysis, cfg)
                    if not sig:
                        continue

                    # Save all
                    store.save_signal(sig)

                    # Check alert thresholds
                    if sig.status != "SIGNAL":
                        continue
                    if sig.quality_score < cfg.ALERT_MIN_QUALITY:
                        continue
                    if sig.confidence_pct < cfg.ALERT_MIN_BOT_CONFIDENCE:
                        continue
                    if sig.data_completeness < cfg.ALERT_MIN_DATA_CONFIDENCE:
                        continue
                    if sig.risk_score > cfg.ALERT_MAX_RISK_SCORE:
                        continue
                    if sig.risk_reward < cfg.ALERT_MIN_RR:
                        continue
                    if cfg.ALERT_REQUIRE_FRESH and bundle.data_age_seconds > cfg.MAX_DATA_AGE_SECONDS:
                        continue

                    signals_found.append(sig)

                except Exception as e:
                    logger.debug(f"Watcher {ticker.symbol} failed: {e}")
                    continue

            # Sort and limit per cycle
            signals_found.sort(key=lambda s: s.quality_score * 0.6 + s.confidence_pct * 0.4, reverse=True)
            to_send = signals_found[: cfg.ALERT_MAX_PER_CYCLE]

            logger.info(f"Watcher cycle done — {len(signals_found)} passed filters, sending {len(to_send)}")

            # Send via Telegram if bot available
            if bot and bot.bot and to_send:
                for sig in to_send:
                    try:
                        text = render_signal_card(sig, mode="beginner", version=cfg.APP_VERSION, release=cfg.APP_RELEASE)
                        # Prepend auto-signal header
                        header = f"🔔 АВТО-СИГНАЛ — {sig.symbol} {sig.direction}\n\n"
                        full = header + text
                        # Send to all alert chat ids
                        for chat_id in cfg.alert_chat_ids:
                            try:
                                await bot.bot.send_message(chat_id=int(chat_id), text=full[:4000])
                            except ValueError:
                                # chat_id may be username?
                                await bot.bot.send_message(chat_id=chat_id, text=full[:4000])
                            except Exception as e:
                                logger.warning(f"Failed to send alert to {chat_id}: {e}")
                        # Also try to send to allowed users if no alert_chat_ids
                        if not cfg.alert_chat_ids:
                            for uid in cfg.allowed_user_ids[:5]:
                                try:
                                    await bot.bot.send_message(chat_id=uid, text=full[:4000])
                                except Exception:
                                    pass
                    except Exception as e:
                        logger.error(f"Failed to send auto signal {sig.symbol}: {e}")

            # Sleep
            await asyncio.sleep(cfg.WATCHER_INTERVAL_SECONDS)

        except asyncio.CancelledError:
            logger.info("Watcher cancelled")
            break
        except Exception as e:
            logger.error(f"Watcher error: {e}")
            await asyncio.sleep(cfg.WATCHER_INTERVAL_SECONDS)


async def run_daemon(cfg: Settings, host: str = "0.0.0.0", port: int = 8400):
    """Run API + watcher + Telegram together."""

    logger.info(build_line(cfg.APP_VERSION, cfg.APP_RELEASE))
    logger.info(f"Starting daemon — exchanges: {cfg.exchanges_list} primary: {cfg.PRIMARY_EXCHANGE}")

    ex_manager = ExchangeManager(cfg)
    store = SignalStore(cfg)

    # API
    from .api.server import create_app
    import uvicorn

    app = create_app(cfg)

    # Telegram bot
    bot = HypeBot(cfg, ex_manager, store) if cfg.TELEGRAM_BOT_TOKEN else None

    # Setup signal handling
    stop_event = asyncio.Event()

    def _signal_handler():
        logger.info("Received stop signal")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass  # Windows

    # Start tasks
    api_task = asyncio.create_task(
        uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="info")).serve()
    )

    watcher_task = asyncio.create_task(watcher_loop(cfg, ex_manager, store, bot))

    bot_task = None
    if bot and bot.bot:
        bot_task = asyncio.create_task(bot.start())
    else:
        logger.warning("Telegram bot disabled — no token")

    # Wait for stop
    try:
        await stop_event.wait()
    finally:
        logger.info("Shutting down daemon...")
        if bot_task:
            bot_task.cancel()
        watcher_task.cancel()
        api_task.cancel()
        await ex_manager.close()
        if bot:
            await bot.stop()
        logger.info("Daemon stopped")
