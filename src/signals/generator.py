"""
Master Signal Generator and Quantitative Setup Factory.
"""
import uuid
from typing import Any

from src.config.constants import EntryType, SignalDirection, SignalTier
from src.config.settings import settings
from src.core.logging import get_logger
from src.core.time_utils import utc_now_ms
from src.data.models import DataQualityReport
from src.regime.classifier import MarketRegimeClassifier
from src.regime.multi_timeframe import MultiTimeframeEngine
from src.signals.analogs import HistoricalAnalogEngine
from src.signals.anomalies import AnomalyDetector
from src.signals.conflict_resolution import ConflictResolver
from src.signals.ensemble import StrategyEnsembleEngine
from src.signals.models import SignalSetup
from src.signals.no_trade import NoTradeEngine
from src.signals.scoring import SignalScorer

logger = get_logger("signals.generator")


class SignalGenerator:
    """
    Master coordinator converting multi-domain market data into validated trading setups.
    """

    @classmethod
    def generate_setup(
        cls,
        entry_features: dict[str, Any],
        macro_features: dict[str, Any] | None = None,
        medium_features: dict[str, Any] | None = None,
        data_quality: DataQualityReport | None = None,
    ) -> SignalSetup:
        symbol = entry_features.get("symbol", "UNKNOWN")
        timeframe = entry_features.get("timeframe", "15m")
        ts = entry_features.get("timestamp_ms", utc_now_ms())
        close = entry_features.get("close", 1.0)
        atr = entry_features.get("atr_14", close * 0.015)

        # 1. Classify Entry Regime
        regime_report = MarketRegimeClassifier.classify(entry_features)

        # 2. Multi-Timeframe Alignment
        mtf_align = None
        if macro_features and medium_features:
            mtf_align = MultiTimeframeEngine.evaluate_alignment(
                symbol=symbol,
                macro_features=macro_features,
                medium_features=medium_features,
                entry_features=entry_features,
            )

        # 3. Strategy Ensemble Evaluation
        top_strat_sig, all_strat_sigs = StrategyEnsembleEngine.evaluate_ensemble(entry_features)

        # 4. Conflict Resolution & Scenario Modeling
        has_conflict, scenario_probs, conflict_reasons = ConflictResolver.resolve_conflicts(all_strat_sigs)

        # 5. Determine Proposed Direction & Levels
        direction: SignalDirection
        entry_type: EntryType
        entry_price: float
        sl: float
        tp1: float
        tp2: float | None
        tp3: float | None
        rr: float
        invalidation: str
        reasons: list[str]
        warnings: list[str]
        strat_name: str

        if top_strat_sig and top_strat_sig.direction != SignalDirection.NO_TRADE:
            direction = top_strat_sig.direction
            entry_type = top_strat_sig.entry_type
            entry_price = top_strat_sig.entry_price
            sl = top_strat_sig.stop_loss
            tp1 = top_strat_sig.take_profit_1
            tp2 = top_strat_sig.take_profit_2
            tp3 = top_strat_sig.take_profit_3
            rr = top_strat_sig.risk_reward_ratio
            invalidation = top_strat_sig.invalidation
            reasons = list(top_strat_sig.reasons)
            warnings = list(top_strat_sig.risk_warnings)
            strat_name = top_strat_sig.strategy_name
        else:
            direction = SignalDirection.NO_TRADE
            entry_type = EntryType.MARKET
            entry_price = close
            sl = close
            tp1 = close
            tp2 = None
            tp3 = None
            rr = 0.0
            invalidation = "No directional edge identified"
            reasons = ["Market structure and indicators currently in neutral equilibrium"]
            warnings = []
            strat_name = "Ensemble"

        # 6. Granular 0-100 Scoring Breakdown
        score_breakdown = SignalScorer.score_setup(direction, entry_features)
        raw_score = score_breakdown.total_score

        # Apply Multi-Timeframe multiplier
        if mtf_align:
            raw_score = min(100.0, raw_score * mtf_align.confidence_multiplier)
            if mtf_align.is_counter_trend:
                warnings.append("Trade direction opposes Macro 4H trend structure (Counter-trend)")

        # 7. Check Anomalies
        anomalies = AnomalyDetector.scan_anomalies(entry_features)
        for a in anomalies:
            warnings.append(f"Anomaly: {a.description}")

        # 8. Historical Analogs Expectancy
        df = entry_features.get("_df")
        analog_results = HistoricalAnalogEngine.evaluate_historical_analogs(
            current_features={"direction": direction.value, **entry_features},
            historical_df=df,
        )

        # 9. Assign Tier
        if direction == SignalDirection.NO_TRADE or raw_score < settings.TIER_WATCH_THRESHOLD:
            tier = SignalTier.NO_TRADE
            direction = SignalDirection.NO_TRADE
        elif raw_score >= settings.TIER_EXTREME_THRESHOLD:
            tier = SignalTier.EXTREME
        elif raw_score >= settings.TIER_STRONG_THRESHOLD:
            tier = SignalTier.STRONG
        elif raw_score >= settings.TIER_VALID_THRESHOLD:
            tier = SignalTier.VALID
        else:
            tier = SignalTier.WATCH

        # Entry Zone formatting
        entry_spread = atr * 0.2
        entry_zone_str = f"${entry_price - entry_spread:.2f} – ${entry_price + entry_spread:.2f}"

        # Dynamic Leverage Calculation (based on SL distance)
        risk_pct = (abs(entry_price - sl) / entry_price) * 100.0 if entry_price > 0 else 2.0
        rec_leverage = max(2, min(settings.MAX_LEVERAGE_CEILING, int(20.0 / max(1.0, risk_pct))))

        signal_id = f"SIG-{symbol}-{int(ts/1000)}-{uuid.uuid4().hex[:6]}"

        setup = SignalSetup(
            signal_id=signal_id,
            symbol=symbol,
            timeframe=timeframe,
            timestamp_ms=ts,
            direction=direction,
            tier=tier,
            score=round(raw_score, 1),
            confidence=round(min(0.95, (raw_score / 100.0) * (0.85 if has_conflict else 1.0)), 2),
            entry_type=entry_type,
            entry_price=round(entry_price, 4),
            entry_zone=entry_zone_str,
            stop_loss=round(sl, 4),
            take_profit_1=round(tp1, 4),
            take_profit_2=round(tp2, 4) if tp2 else None,
            take_profit_3=round(tp3, 4) if tp3 else None,
            risk_reward_ratio=round(rr, 2),
            recommended_leverage=rec_leverage,
            invalidation_condition=invalidation,
            primary_reasons=reasons,
            risk_factors=warnings,
            score_breakdown=score_breakdown,
            scenario_probabilities=scenario_probs,
            market_regime=regime_report.regime.value,
            data_quality_score=data_quality.quality_score if data_quality else 1.0,
            historical_analog_expectancy_r=analog_results["expectancy_r"],
            analog_sample_size=analog_results["analog_count"],
            analog_win_rate_pct=analog_results["win_rate_pct"],
            strategy_source=strat_name,
        )

        # 10. Check No-Trade Suppression Gates
        should_suppress, suppress_reasons = NoTradeEngine.should_suppress_signal(
            setup=setup,
            data_quality=data_quality,
            has_model_conflict=has_conflict,
            macro_counter_trend=mtf_align.is_counter_trend if mtf_align else False,
        )

        if should_suppress and setup.direction != SignalDirection.NO_TRADE:
            setup.direction = SignalDirection.NO_TRADE
            setup.tier = SignalTier.NO_TRADE
            setup.risk_factors.extend(suppress_reasons)

        return setup
