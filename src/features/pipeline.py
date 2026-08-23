"""
Unified Feature Engineering Pipeline.
"""
from typing import Any

import pandas as pd

from src.data.models import (
    CandleData,
    FundingRateData,
    OpenInterestData,
    OrderBookData,
    TickerData,
)
from src.features.futures_derivatives import DerivativesFeatureEngine
from src.features.liquidity import LiquidityAnalyzer
from src.features.market_structure import MarketStructureAnalyzer
from src.features.order_flow import OrderFlowAnalyzer
from src.features.technical import TechnicalIndicators
from src.features.volatility import VolatilityAnalyzer


class FeaturePipeline:
    """
    Orchestrates the computation, alignment, and normalization of all market features.
    """

    def __init__(self):
        self.ms_analyzer = MarketStructureAnalyzer(fractal_window=3)
        self.liq_analyzer = LiquidityAnalyzer(eq_tolerance_pct=0.20)

    def candles_to_dataframe(self, candles: list[CandleData]) -> pd.DataFrame:
        """Convert list of CandleData models into a pandas DataFrame."""
        records = [c.model_dump() for c in candles]
        df = pd.DataFrame.from_records(records)
        if not df.empty:
            df.sort_values("timestamp_ms", inplace=True)
            df.reset_index(drop=True, inplace=True)
        return df

    def compute_feature_matrix(
        self,
        candles: list[CandleData],
        orderbook: OrderBookData | None = None,
        ticker: TickerData | None = None,
        funding: FundingRateData | None = None,
        open_interest: OpenInterestData | None = None,
        btc_candles: list[CandleData] | None = None,
    ) -> dict[str, Any]:
        """
        Compute the comprehensive multi-domain feature dictionary for a symbol.
        """
        if not candles or len(candles) < 20:
            return {}

        df = self.candles_to_dataframe(candles)

        # 1. Technical Indicators
        df = TechnicalIndicators.compute_all(df)
        df = OrderFlowAnalyzer.compute_cvd(df)

        last_row = df.iloc[-1]
        cur_close = float(last_row["close"])

        # 2. Market Structure
        structure_info = self.ms_analyzer.analyze_structure(df)

        # 3. Liquidity
        eq_pools = self.liq_analyzer.find_equal_highs_lows(df)
        sweep_info = self.liq_analyzer.detect_liquidity_sweep(df)
        fvgs = self.liq_analyzer.detect_fair_value_gaps(df)

        # 4. Volatility
        vol_info = VolatilityAnalyzer.compute_volatility_metrics(df)

        # 5. Order Flow
        ob_info = (
            OrderFlowAnalyzer.analyze_orderbook(orderbook)
            if orderbook
            else {
                "imbalance": 0.0,
                "spread_pct": 0.0,
                "bid_depth_usd": 0.0,
                "ask_depth_usd": 0.0,
                "liquidity_walls": [],
                "suspicious_liquidity": False,
            }
        )

        # 6. Derivatives
        current_funding_rate = funding.funding_rate if funding else 0.0001
        funding_z = DerivativesFeatureEngine.calculate_funding_z_score(current_funding_rate)

        # 24h price change and OI change
        price_24h_pct = float(((cur_close - df["close"].iloc[-min(len(df), 96)]) / df["close"].iloc[-min(len(df), 96)]) * 100.0)
        oi_val = open_interest.open_interest if open_interest else 0.0
        pos_info = DerivativesFeatureEngine.analyze_price_oi_relationship(price_24h_pct, 0.0)

        # Structure summary
        features = {
            "symbol": candles[0].symbol,
            "timeframe": candles[0].timeframe,
            "timestamp_ms": int(last_row["timestamp_ms"]),
            "close": cur_close,
            "open": float(last_row["open"]),
            "high": float(last_row["high"]),
            "low": float(last_row["low"]),
            "volume": float(last_row["volume"]),
            # Technicals
            "ema_9": float(last_row.get("ema_9", cur_close)),
            "ema_21": float(last_row.get("ema_21", cur_close)),
            "ema_50": float(last_row.get("ema_50", cur_close)),
            "ema_200": float(last_row.get("ema_200", cur_close)),
            "vwap": float(last_row.get("vwap", cur_close)),
            "vwap_dist_pct": float(last_row.get("vwap_dist_pct", 0.0)),
            "rsi_14": float(last_row.get("rsi_14", 50.0)),
            "adx_14": float(last_row.get("adx_14", 20.0)),
            "atr_14": float(last_row.get("atr_14", cur_close * 0.01)),
            "atr_pct": float(last_row.get("atr_pct", 1.0)),
            "bb_width": float(last_row.get("bb_width", 2.0)),
            "bb_percent_b": float(last_row.get("bb_percent_b", 0.5)),
            "macd_line": float(last_row.get("macd_line", 0.0)),
            "macd_signal": float(last_row.get("macd_signal", 0.0)),
            "macd_hist": float(last_row.get("macd_hist", 0.0)),
            "stoch_k": float(last_row.get("stoch_k", 50.0)),
            # Market Structure
            "structure_state": structure_info["structure_state"],
            "structure_score": structure_info["structure_score"],
            "bos_bullish": structure_info["bos_bullish"],
            "bos_bearish": structure_info["bos_bearish"],
            "choch_bullish": structure_info["choch_bullish"],
            "choch_bearish": structure_info["choch_bearish"],
            "last_swing_high": structure_info["last_swing_high"],
            "last_swing_low": structure_info["last_swing_low"],
            # Liquidity
            "sweep_bullish": sweep_info["sweep_bullish"],
            "sweep_bearish": sweep_info["sweep_bearish"],
            "liquidity_pools_count": len(eq_pools),
            "fvgs_count": len(fvgs),
            # Volatility
            "realized_vol_pct": vol_info["realized_vol_pct"],
            "atr_percentile": vol_info["atr_percentile"],
            "bb_width_percentile": vol_info["bb_width_percentile"],
            "volatility_regime": vol_info["volatility_regime"].value,
            "volatility_trend": vol_info["volatility_trend"].value,
            "is_squeeze": vol_info["is_squeeze"],
            # Order Flow
            "orderbook_imbalance": ob_info["imbalance"],
            "spread_pct": ob_info["spread_pct"],
            "suspicious_liquidity": ob_info["suspicious_liquidity"],
            "cvd_divergence": float(last_row.get("cvd_divergence", 0.0)),
            # Derivatives
            "funding_rate": current_funding_rate,
            "funding_z_score": funding_z,
            "open_interest": oi_val,
            "positioning_state": pos_info["positioning_state"],
            # Raw Dataframe for deeper historical analysis
            "_df": df,
        }

        return features
