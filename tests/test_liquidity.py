"""
Tests for Liquidity Pools, Sweeps, and Order Flow Analysis.
"""
from src.features.liquidity import LiquidityAnalyzer
from src.features.order_flow import OrderFlowAnalyzer
from src.features.pipeline import FeaturePipeline


def test_liquidity_analysis(sample_candles):
    pipeline = FeaturePipeline()
    df = pipeline.candles_to_dataframe(sample_candles)

    liq_analyzer = LiquidityAnalyzer()
    pools = liq_analyzer.find_equal_highs_lows(df)
    assert isinstance(pools, list)

    sweep_info = liq_analyzer.detect_liquidity_sweep(df)
    assert "sweep_bullish" in sweep_info
    assert "sweep_bearish" in sweep_info


def test_order_flow_analysis(sample_orderbook, sample_candles):
    ob_res = OrderFlowAnalyzer.analyze_orderbook(sample_orderbook)
    assert "imbalance" in ob_res
    assert -1.0 <= ob_res["imbalance"] <= 1.0
    assert ob_res["spread_pct"] >= 0.0

    pipeline = FeaturePipeline()
    df = pipeline.candles_to_dataframe(sample_candles)
    df_cvd = OrderFlowAnalyzer.compute_cvd(df)
    assert "cvd" in df_cvd.columns
    assert "volume_delta" in df_cvd.columns
