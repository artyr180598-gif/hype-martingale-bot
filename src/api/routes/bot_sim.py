"""
Telegram Bot Command Simulation and Web Control Interface.
"""
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from src.ai.assistant import AIAssistant
from src.backtesting.engine import BacktestEngine
from src.bot.client import TelegramBotClient
from src.bot.formatters import BotFormatters
from src.bot.handlers import BotHandlers, paper_engine
from src.data.adapters.binance import BinanceFuturesAdapter
from src.data.downloader import HistoricalDataDownloader
from src.intelligence.news_fetcher import NewsFetcher
from src.scanner.market_scanner import MarketScanner
from src.strategies.registry import StrategyRegistry

router = APIRouter(prefix="/api/v1/bot", tags=["Bot Terminal"])


class CommandRequest(BaseModel):
    command: str


class CommandResponse(BaseModel):
    command: str
    reply_text: str
    data: dict[str, Any] | None = None


@router.post("/execute", response_model=CommandResponse)
async def execute_command(req: CommandRequest):
    cmd = req.command.strip()
    downloader = HistoricalDataDownloader(BinanceFuturesAdapter())
    scanner = MarketScanner()

    if cmd.startswith("/start"):
        text = (
            "🚀 **Quantitative Crypto Futures Intelligence Platform**\n\n"
            "Платформа сканирует рынок фьючерсов, анализирует Order Flow, "
            "Market Structure, фандинг, открытый интерес, волатильность и мульти-таймфреймовые тренды.\n\n"
            "Доступные разделы: /market, /top, /analyze BTC, /backtest, /paper, /news, /strategies, /settings"
        )
        return CommandResponse(command=cmd, reply_text=text)

    elif cmd.startswith("/market"):
        adapter = BinanceFuturesAdapter()
        tickers_data = []
        for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"]:
            try:
                t = await adapter.fetch_ticker(sym)
                tickers_data.append(t.model_dump())
            except Exception:
                pass

        breadth_data = {
            "breadth_state": "BULLISH_EXPANSION",
            "pct_above_ema50": 68.0,
            "advance_decline_ratio": 1.8,
        }
        text = BotFormatters.format_market_overview(tickers_data, breadth_data)
        return CommandResponse(command=cmd, reply_text=text, data={"tickers": tickers_data})

    elif cmd.startswith("/top"):
        setups = await scanner.scan_market()
        valid_setups = [s for s in setups if s.score >= 60.0 and s.direction.value != "NO_TRADE"]
        if not valid_setups:
            text = "⚪ **В настоящее время статистического преимущества недостаточно.**\nСистема находится в режиме Capital Protection (NO TRADE)."
        else:
            cards = [BotFormatters.format_signal(s) for s in valid_setups[:3]]
            text = "\n\n" + ("=" * 40) + "\n\n".join(cards)
        return CommandResponse(command=cmd, reply_text=text, data={"setups": [s.model_dump() for s in setups]})

    elif cmd.startswith("/analyze"):
        parts = cmd.split()
        sym = parts[1].upper() if len(parts) > 1 else "BTCUSDT"
        if not sym.endswith("USDT") and not sym.endswith("USDC"):
            sym += "USDT"
        setup = await scanner.scan_symbol(sym)
        if not setup:
            return CommandResponse(command=cmd, reply_text=f"⚠️ Не удалось получить данные по {sym}.")
        text = BotFormatters.format_signal(setup)
        return CommandResponse(command=cmd, reply_text=text, data=setup.model_dump())

    elif cmd.startswith("/backtest"):
        candles = await downloader.get_or_download_candles("BTCUSDT", "15m", lookback_bars=500)
        strat = StrategyRegistry.list_active()[0]
        engine = BacktestEngine(strategy=strat)
        res = engine.run(candles)
        text = BotFormatters.format_backtest_report(res.metrics, "BTCUSDT", strat.name)
        return CommandResponse(command=cmd, reply_text=text, data={"metrics": res.metrics.__dict__})

    elif cmd.startswith("/paper"):
        text = BotFormatters.format_paper_portfolio(paper_engine.portfolio)
        return CommandResponse(command=cmd, reply_text=text, data={"portfolio": paper_engine.portfolio.model_dump()})

    elif cmd.startswith("/news"):
        articles = await NewsFetcher.fetch_latest_news(limit=5)
        lines = ["📰 **ПОСЛЕДНИЕ СОБЫТИЯ И НОВОСТНОЙ СЕНТИМЕНТ:**\n"]
        for a in articles:
            s_emoji = "🟢" if a["sentiment"] == "BULLISH" else ("🔴" if a["sentiment"] == "BEARISH" else "⚪")
            lines.append(f"{s_emoji} **{a['title']}**\n   _{a['source']}_ | Влияние: `{a['impact']}`\n")
        return CommandResponse(command=cmd, reply_text="\n".join(lines), data={"articles": articles})

    elif cmd.startswith("/strategies"):
        strats = StrategyRegistry.list_all()
        lines = ["🧠 **РЕЕСТР КВАНТОВЫХ СТРАТЕГИЙ:**\n"]
        for s in strats:
            regimes_str = ", ".join(r.value for r in s.expected_regimes) if s.expected_regimes else "ALL"
            lines.append(f"• **{s.name}** (v{s.version})\n  Статус: `{s.status.value}` | Режимы: `{regimes_str}`\n")
        return CommandResponse(command=cmd, reply_text="\n".join(lines), data={"strategies": [s.name for s in strats]})

    elif cmd.startswith("/settings"):
        text = (
            "⚙️ **НАСТРОЙКИ ПРОФИЛЯ РИСКА:**\n\n"
            "• Conservative (0.75% / trade)\n"
            "• Balanced (1.50% / trade) [ACTIVE]\n"
            "• Aggressive (2.50% / trade)\n\n"
            "Максимальное плечо: 10x | Защита от просадки: 10%"
        )
        return CommandResponse(command=cmd, reply_text=text)

    elif cmd.startswith("/help"):
        text = (
            "📚 **СПРАВКА ПО КОМАНДАМ:**\n\n"
            "• `/start` — Главное меню\n"
            "• `/market` — Обзор рынка и широтные метрики\n"
            "• `/top` — Топ подтвержденных сетапов\n"
            "• `/analyze BTC` — Полный квантовый анализ актива\n"
            "• `/backtest` — Запуск бэктеста\n"
            "• `/paper` — Виртуальный торговый портфель\n"
            "• `/news` — Анализ новостей и сентимента\n"
            "• `/strategies` — Список активных стратегий\n"
            "• `/settings` — Настройки риска"
        )
        return CommandResponse(command=cmd, reply_text=text)

    else:
        # Fallback to AI Assistant
        answer = await AIAssistant.process_user_query(cmd)
        return CommandResponse(command=cmd, reply_text=answer)
