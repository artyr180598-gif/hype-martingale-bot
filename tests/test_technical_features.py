"""
Tests for Technical Indicators and Feature Pipeline.
"""
from src.features.pipeline import FeaturePipeline
from src.features.technical import TechnicalIndicators, calculate_fibonacci_levels


def test_technical_indicators_computation(sample_candles):
    pipeline = FeaturePipeline()
    df = pipeline.candles_to_dataframe(sample_candles)
    df_calc = TechnicalIndicators.compute_all(df)

    assert "ema_9" in df_calc.columns
    assert "ema_21" in df_calc.columns
    assert "ema_50" in df_calc.columns
    assert "vwap" in df_calc.columns
    assert "rsi_14" in df_calc.columns
    assert "macd_line" in df_calc.columns
    assert "atr_14" in df_calc.columns
    assert "bb_width" in df_calc.columns
    assert "adx_14" in df_calc.columns

    # Verify RSI is bounded [0, 100]
    rsi_vals = df_calc["rsi_14"].dropna()
    assert (rsi_vals >= 0.0).all()
    assert (rsi_vals <= 100.0).all()


def test_fibonacci_levels():
    fibs = calculate_fibonacci_levels(high=60000.0, low=50000.0)
    assert fibs["fib_0"] == 50000.0
    assert fibs["fib_1.0"] == 60000.0
    assert fibs["fib_0.500"] == 55000.0
    assert fibs["fib_0.618"] == 56180.0
