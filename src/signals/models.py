"""
Comprehensive Signal Engine Models and Setup DTOs.
"""
from pydantic import BaseModel, Field

from src.config.constants import EntryType, SignalDirection, SignalTier


class ScoreBreakdown(BaseModel):
    trend: float = 0.0             # max 15.0
    market_structure: float = 0.0  # max 15.0
    order_flow: float = 0.0        # max 15.0
    volatility: float = 0.0        # max 10.0
    open_interest: float = 0.0     # max 10.0
    volume: float = 0.0            # max 10.0
    momentum: float = 0.0          # max 10.0
    funding: float = 0.0           # max 5.0
    liquidations: float = 0.0      # max 5.0
    market_breadth: float = 0.0    # max 5.0
    sentiment: float = 0.0         # max 5.0

    @property
    def total_score(self) -> float:
        return sum([
            self.trend,
            self.market_structure,
            self.order_flow,
            self.volatility,
            self.open_interest,
            self.volume,
            self.momentum,
            self.funding,
            self.liquidations,
            self.market_breadth,
            self.sentiment,
        ])


class ScenarioProbabilities(BaseModel):
    long_probability_pct: float
    short_probability_pct: float
    no_trade_probability_pct: float


class SignalSetup(BaseModel):
    signal_id: str
    symbol: str
    timeframe: str
    timestamp_ms: int
    direction: SignalDirection
    tier: SignalTier
    score: float                         # 0.0 to 100.0
    confidence: float                    # 0.0 to 1.0

    entry_type: EntryType
    entry_price: float
    entry_zone: str                      # e.g. "$64,200 - $64,450"
    stop_loss: float
    take_profit_1: float
    take_profit_2: float | None = None
    take_profit_3: float | None = None
    risk_reward_ratio: float

    recommended_leverage: int = 5
    invalidation_condition: str
    primary_reasons: list[str] = Field(default_factory=list)
    risk_factors: list[str] = Field(default_factory=list)
    score_breakdown: ScoreBreakdown
    scenario_probabilities: ScenarioProbabilities

    market_regime: str
    data_quality_score: float = 1.0

    # Historical Analogs
    historical_analog_expectancy_r: float | None = None
    analog_sample_size: int = 0
    analog_win_rate_pct: float | None = None

    # Meta
    strategy_source: str = "Ensemble"
    strategy_version: str = "4.0.0"
