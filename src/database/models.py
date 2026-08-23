"""
SQLAlchemy ORM Database Schema for Quantitative Futures Intelligence Platform.
"""
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, relationship

from src.core.time_utils import utc_now


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, unique=True, index=True, nullable=False)
    username = Column(String(64), nullable=True)
    first_name = Column(String(128), nullable=True)
    risk_profile = Column(String(32), default="BALANCED", nullable=False)
    preferred_exchange = Column(String(32), default="binance", nullable=False)
    min_alert_score = Column(Float, default=75.0, nullable=False)
    notifications_enabled = Column(Boolean, default=True, nullable=False)
    timezone_offset_hours = Column(Float, default=0.0, nullable=False)
    is_admin = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)

    watchlists = relationship("UserWatchlist", back_populates="user", cascade="all, delete-orphan")
    paper_positions = relationship("PaperPosition", back_populates="user")
    alerts = relationship("AlertLog", back_populates="user")


class UserWatchlist(Base):
    __tablename__ = "user_watchlists"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    symbol = Column(String(32), nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)

    user = relationship("User", back_populates="watchlists")

    __table_args__ = (
        Index("idx_user_symbol_unique", "user_id", "symbol", unique=True),
    )


class Candle(Base):
    __tablename__ = "candles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(32), nullable=False, index=True)
    timeframe = Column(String(16), nullable=False, index=True)
    timestamp_ms = Column(BigInteger, nullable=False, index=True)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)
    quote_volume = Column(Float, default=0.0, nullable=False)
    trades_count = Column(Integer, default=0, nullable=False)
    taker_buy_volume = Column(Float, default=0.0, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)

    __table_args__ = (
        Index("idx_symbol_tf_ts", "symbol", "timeframe", "timestamp_ms", unique=True),
    )


class FundingRate(Base):
    __tablename__ = "funding_rates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(32), nullable=False, index=True)
    timestamp_ms = Column(BigInteger, nullable=False, index=True)
    rate = Column(Float, nullable=False)
    predicted_rate = Column(Float, default=0.0, nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)

    __table_args__ = (
        Index("idx_funding_symbol_ts", "symbol", "timestamp_ms", unique=True),
    )


class OpenInterest(Base):
    __tablename__ = "open_interest"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(32), nullable=False, index=True)
    timestamp_ms = Column(BigInteger, nullable=False, index=True)
    open_interest = Column(Float, nullable=False)
    open_interest_usd = Column(Float, default=0.0, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)

    __table_args__ = (
        Index("idx_oi_symbol_ts", "symbol", "timestamp_ms", unique=True),
    )


class LiquidationEvent(Base):
    __tablename__ = "liquidation_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(32), nullable=False, index=True)
    timestamp_ms = Column(BigInteger, nullable=False, index=True)
    side = Column(String(16), nullable=False)  # BUY or SELL
    price = Column(Float, nullable=False)
    quantity = Column(Float, nullable=False)
    usd_value = Column(Float, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)


class MarketRegimeLog(Base):
    __tablename__ = "market_regimes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(32), nullable=False, index=True)
    timeframe = Column(String(16), nullable=False)
    timestamp_ms = Column(BigInteger, nullable=False, index=True)
    regime = Column(String(32), nullable=False)
    volatility_regime = Column(String(32), nullable=False)
    volatility_trend = Column(String(32), nullable=False)
    confidence = Column(Float, nullable=False)
    adx = Column(Float, nullable=True)
    atr = Column(Float, nullable=True)
    metrics_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)


class SignalRecord(Base):
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    signal_id = Column(String(64), unique=True, index=True, nullable=False)
    symbol = Column(String(32), nullable=False, index=True)
    timeframe = Column(String(16), nullable=False)
    timestamp_ms = Column(BigInteger, nullable=False, index=True)
    direction = Column(String(16), nullable=False)  # LONG, SHORT, NO_TRADE
    tier = Column(String(16), nullable=False)       # EXTREME, STRONG, VALID, WATCH, NO_TRADE
    score = Column(Float, nullable=False)
    confidence = Column(Float, nullable=False)

    entry_price = Column(Float, nullable=False)
    entry_type = Column(String(32), default="MARKET", nullable=False)
    stop_loss = Column(Float, nullable=False)
    take_profit_1 = Column(Float, nullable=False)
    take_profit_2 = Column(Float, nullable=True)
    take_profit_3 = Column(Float, nullable=True)
    risk_reward_ratio = Column(Float, nullable=False)

    recommended_leverage = Column(Integer, default=5, nullable=False)
    invalidation_condition = Column(Text, nullable=False)
    primary_reasons = Column(JSON, nullable=False)   # List[str]
    risk_factors = Column(JSON, nullable=False)      # List[str]
    score_breakdown = Column(JSON, nullable=False)   # Dict[str, float]
    market_regime = Column(String(32), nullable=False)

    strategy_version = Column(String(32), default="1.0.0", nullable=False)
    status = Column(String(32), default="ACTIVE", nullable=False)  # ACTIVE, HIT_TP, HIT_SL, EXPIRED, INVALIDATED

    # Post-signal evaluation metrics
    actual_outcome = Column(String(32), nullable=True)  # WIN, LOSS, BREAKEVEN, INVALIDATED
    realized_r = Column(Float, nullable=True)
    mfe_percent = Column(Float, nullable=True)  # Max Favorable Excursion
    mae_percent = Column(Float, nullable=True)  # Max Adverse Excursion
    closed_at_ms = Column(BigInteger, nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)


class BacktestRecord(Base):
    __tablename__ = "backtests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    backtest_id = Column(String(64), unique=True, index=True, nullable=False)
    symbol = Column(String(32), nullable=False)
    timeframe = Column(String(16), nullable=False)
    strategy_name = Column(String(64), nullable=False)
    start_time_ms = Column(BigInteger, nullable=False)
    end_time_ms = Column(BigInteger, nullable=False)
    initial_balance = Column(Float, nullable=False)
    final_equity = Column(Float, nullable=False)

    total_return_pct = Column(Float, nullable=False)
    cagr_pct = Column(Float, nullable=True)
    sharpe_ratio = Column(Float, nullable=False)
    sortino_ratio = Column(Float, nullable=False)
    calmar_ratio = Column(Float, nullable=False)
    max_drawdown_pct = Column(Float, nullable=False)
    win_rate_pct = Column(Float, nullable=False)
    profit_factor = Column(Float, nullable=False)
    expectancy_r = Column(Float, nullable=False)

    total_trades = Column(Integer, nullable=False)
    winning_trades = Column(Integer, nullable=False)
    losing_trades = Column(Integer, nullable=False)
    liquidations_count = Column(Integer, default=0, nullable=False)
    total_fees_paid = Column(Float, default=0.0, nullable=False)
    total_funding_paid = Column(Float, default=0.0, nullable=False)

    parameters_json = Column(JSON, nullable=False)
    regime_breakdown_json = Column(JSON, nullable=True)
    equity_curve_summary = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)


class PaperPosition(Base):
    __tablename__ = "paper_positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    symbol = Column(String(32), nullable=False, index=True)
    side = Column(String(16), nullable=False)  # LONG, SHORT
    quantity = Column(Float, nullable=False)
    entry_price = Column(Float, nullable=False)
    current_price = Column(Float, nullable=False)
    leverage = Column(Integer, default=1, nullable=False)
    margin_allocated = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=True)
    take_profit_1 = Column(Float, nullable=True)
    take_profit_2 = Column(Float, nullable=True)
    unrealized_pnl = Column(Float, default=0.0, nullable=False)
    realized_pnl = Column(Float, default=0.0, nullable=False)
    total_commission = Column(Float, default=0.0, nullable=False)
    total_funding = Column(Float, default=0.0, nullable=False)
    status = Column(String(32), default="OPEN", nullable=False)  # OPEN, CLOSED
    opened_at_ms = Column(BigInteger, nullable=False)
    closed_at_ms = Column(BigInteger, nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)

    user = relationship("User", back_populates="paper_positions")


class PaperTrade(Base):
    __tablename__ = "paper_trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    position_id = Column(BigInteger, nullable=True, index=True)
    symbol = Column(String(32), nullable=False, index=True)
    side = Column(String(16), nullable=False)  # BUY, SELL
    order_type = Column(String(32), default="MARKET", nullable=False)
    price = Column(Float, nullable=False)
    quantity = Column(Float, nullable=False)
    fee = Column(Float, default=0.0, nullable=False)
    realized_pnl = Column(Float, default=0.0, nullable=False)
    timestamp_ms = Column(BigInteger, nullable=False, index=True)
    exit_reason = Column(String(64), nullable=True)  # TP1, TP2, TP3, SL, MANUAL, LIQUIDATION
    created_at = Column(DateTime, default=utc_now, nullable=False)


class AlertLog(Base):
    __tablename__ = "alert_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    event_hash = Column(String(64), unique=True, index=True, nullable=False)
    symbol = Column(String(32), nullable=False, index=True)
    alert_type = Column(String(64), nullable=False)
    score = Column(Float, nullable=True)
    message = Column(Text, nullable=False)
    sent_successfully = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)

    user = relationship("User", back_populates="alerts")


class NewsArticle(Base):
    __tablename__ = "news_articles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(String(128), unique=True, index=True, nullable=False)
    source = Column(String(64), nullable=False)
    title = Column(String(512), nullable=False)
    url = Column(String(1024), nullable=False)
    published_at_ms = Column(BigInteger, nullable=False, index=True)
    sentiment = Column(String(32), default="NEUTRAL", nullable=False)
    sentiment_score = Column(Float, default=0.0, nullable=False)
    impact = Column(String(32), default="LOW", nullable=False)
    asset_tags = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)
