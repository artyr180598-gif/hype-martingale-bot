"""
Tests for Market Regime Classifier and Strategies.
"""
from src.config.constants import SignalDirection
from src.features.pipeline import FeaturePipeline
from src.regime.classifier import MarketRegimeClassifier
from src.strategies.breakout import BreakoutStrategy
from src.strategies.mean_reversion import MeanReversionStrategy
from src.strategies.trend_following import TrendFollowingStrategy


def test_market_regime_classification(sample_candles, sample_orderbook):
    pipeline = FeaturePipeline()
    feat = pipeline.compute_feature_matrix(sample_candles, orderbook=sample_orderbook)
    report = MarketRegimeClassifier.classify(feat)

    assert report.regime.value is not None
    assert report.confidence >= 0.0
    assert len(report.favorable_strategies) > 0


def test_strategy_evaluation(sample_candles, sample_orderbook):
    pipeline = FeaturePipeline()
    feat = pipeline.compute_feature_matrix(sample_candles, orderbook=sample_orderbook)

    strat_trend = TrendFollowingStrategy()
    sig_trend = strat_trend.evaluate(feat)
    assert sig_trend.direction in (SignalDirection.LONG, SignalDirection.SHORT, SignalDirection.NO_TRADE)
    assert 0.0 <= sig_trend.score <= 100.0

    strat_break = BreakoutStrategy()
    sig_break = strat_break.evaluate(feat)
    assert 0.0 <= sig_break.score <= 100.0

    strat_mr = MeanReversionStrategy()
    sig_mr = strat_mr.evaluate(feat)
    assert 0.0 <= sig_mr.score <= 100.0
