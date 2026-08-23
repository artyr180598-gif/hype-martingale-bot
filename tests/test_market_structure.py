"""
Tests for Market Structure Engine.
"""
from src.features.market_structure import MarketStructureAnalyzer
from src.features.pipeline import FeaturePipeline


def test_market_structure_analysis(sample_candles):
    pipeline = FeaturePipeline()
    df = pipeline.candles_to_dataframe(sample_candles)

    analyzer = MarketStructureAnalyzer(fractal_window=3)
    swings = analyzer.find_swing_points(df)
    assert len(swings) > 0

    res = analyzer.analyze_structure(df)
    assert res["structure_state"] in ("BULLISH", "BEARISH", "RANGE")
    assert "bos_bullish" in res
    assert "bos_bearish" in res
    assert "last_swing_high" in res
    assert "last_swing_low" in res
