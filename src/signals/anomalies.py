"""
Market Anomaly and Extreme Event Detection Engine.
"""
from dataclasses import dataclass
from typing import Any


@dataclass
class MarketAnomaly:
    symbol: str
    anomaly_type: str
    severity: str        # "LOW", "MEDIUM", "HIGH", "CRITICAL"
    description: str
    risk_action: str     # "MONITOR", "DEGRADE_CONFIDENCE", "SUPPRESS_SIGNALS"


class AnomalyDetector:
    """
    Detects market aberrations, flash crash risks, volume spikes, and leverage explosions.
    """

    @classmethod
    def scan_anomalies(cls, features: dict[str, Any]) -> list[MarketAnomaly]:
        anomalies: list[MarketAnomaly] = []
        if not features:
            return anomalies

        symbol = features.get("symbol", "UNKNOWN")
        df = features.get("_df")

        # 1. Volume Surge Anomaly (> 3.5x 20-bar average)
        if df is not None and len(df) >= 20:
            cur_vol = float(df["volume"].iloc[-1])
            avg_vol = float(df["volume"].iloc[-20:].mean())
            if avg_vol > 0 and cur_vol > avg_vol * 3.5:
                anomalies.append(
                    MarketAnomaly(
                        symbol=symbol,
                        anomaly_type="VOLUME_SPIKE",
                        severity="HIGH",
                        description=f"Volume is {cur_vol/avg_vol:.1f}x higher than 20-bar baseline",
                        risk_action="MONITOR",
                    )
                )

        # 2. Extreme Funding Z-score
        funding_z = features.get("funding_z_score", 0.0)
        if abs(funding_z) > 3.0:
            anomalies.append(
                MarketAnomaly(
                    symbol=symbol,
                    anomaly_type="EXTREME_FUNDING_ABERRATION",
                    severity="CRITICAL",
                    description=f"Funding Z-Score reached extreme level ({funding_z:.2f}) — severe liquidation cascade risk",
                    risk_action="DEGRADE_CONFIDENCE",
                )
            )

        # 3. Microstructural Order Book Anomaly
        if features.get("suspicious_liquidity", False):
            anomalies.append(
                MarketAnomaly(
                    symbol=symbol,
                    anomaly_type="SUSPICIOUS_LIQUIDITY_BEHAVIOR",
                    severity="HIGH",
                    description="Sudden orderbook liquidity withdrawal and abnormal spread widening",
                    risk_action="SUPPRESS_SIGNALS",
                )
            )

        return anomalies
