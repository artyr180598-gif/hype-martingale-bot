"""
Tests for Backtesting Engine, Walk-Forward, and Monte Carlo.
"""
from src.backtesting.engine import BacktestEngine
from src.backtesting.monte_carlo import MonteCarloSimulator
from src.intelligence.sentiment_analyzer import SentimentAnalyzer
from src.paper.engine import PaperTradingEngine
from src.strategies.trend_following import TrendFollowingStrategy


def test_backtest_engine_run(sample_candles):
    strat = TrendFollowingStrategy()
    engine = BacktestEngine(strategy=strat, initial_balance=10000.0)
    res = engine.run(sample_candles)

    assert res.metrics.final_equity > 0
    assert len(res.equity_curve) > 0
    assert res.symbol == "BTCUSDT"
    assert res.strategy_name == strat.name


def test_monte_carlo_simulation():
    sample_trades = [
        {"net_pnl": 150.0, "side": "LONG"},
        {"net_pnl": -100.0, "side": "SHORT"},
        {"net_pnl": 200.0, "side": "LONG"},
        {"net_pnl": -80.0, "side": "LONG"},
        {"net_pnl": 300.0, "side": "SHORT"},
    ]
    mc = MonteCarloSimulator.run_simulation(sample_trades, initial_balance=10000.0, num_simulations=100)
    assert mc.total_simulations == 100
    assert mc.median_ending_equity > 0
    assert mc.max_drawdown_95th_percentile >= 0.0


def test_paper_trading_simulator():
    engine = PaperTradingEngine(initial_balance=10000.0)
    assert engine.portfolio.cash_balance == 10000.0
    assert engine.portfolio.available_balance == 10000.0


def test_news_sentiment():
    bull_res = SentimentAnalyzer.analyze_text("Bitcoin surges to new highs as institutional ETF adoption accelerates")
    assert bull_res.sentiment.value == "BULLISH"
    assert bull_res.score > 0

    bear_res = SentimentAnalyzer.analyze_text("Massive exploit hack drains protocol causing market dump")
    assert bear_res.sentiment.value == "BEARISH"
    assert bear_res.score < 0
