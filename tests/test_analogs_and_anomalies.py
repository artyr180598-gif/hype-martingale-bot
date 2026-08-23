"""
Tests for Historical Analogs and Anomaly Detection.
"""
from src.features.pipeline import FeaturePipeline
from src.signals.analogs import HistoricalAnalogEngine
from src.signals.anomalies import AnomalyDetector


def test_historical_analogs(sample_candles):
    pipeline = FeaturePipeline()
    df = pipeline.candles_to_dataframe(sample_candles)
    feat = pipeline.compute_feature_matrix(sample_candles)

    analogs = HistoricalAnalogEngine.evaluate_historical_analogs(
        current_features=feat,
        historical_df=df,
        top_k=10,
    )
    assert "win_rate_pct" in analogs
    assert "expectancy_r" in analogs


def test_anomaly_detection(sample_candles):
    pipeline = FeaturePipeline()
    feat = pipeline.compute_feature_matrix(sample_candles)

    # Inject simulated volume spike
    df = feat["_df"].copy()
    df.loc[df.index[-1], "volume"] = df["volume"].mean() * 5.0
    feat["_df"] = df

    anomalies = AnomalyDetector.scan_anomalies(feat)
    assert len(anomalies) > 0
    assert any(a.anomaly_type == "VOLUME_SPIKE" for a in anomalies)
