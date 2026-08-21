"""Create initial market intelligence tables.

Revision ID: 0001_initial_market_schema
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial_market_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assets",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False, unique=True),
        sa.Column("base_asset", sa.String(32), nullable=False),
        sa.Column("quote_asset", sa.String(16), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "markets",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("asset_id", sa.BigInteger(), sa.ForeignKey("assets.id"), nullable=False),
        sa.Column("exchange", sa.String(32), nullable=False),
        sa.Column("market_type", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("exchange", "symbol", name="uq_markets_exchange_symbol"),
    )
    op.create_table(
        "candles",
        sa.Column("market_id", sa.BigInteger(), sa.ForeignKey("markets.id"), nullable=False),
        sa.Column("timeframe", sa.String(16), nullable=False),
        sa.Column("open_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("close_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(38, 18), nullable=False),
        sa.Column("high", sa.Numeric(38, 18), nullable=False),
        sa.Column("low", sa.Numeric(38, 18), nullable=False),
        sa.Column("close", sa.Numeric(38, 18), nullable=False),
        sa.Column("volume", sa.Numeric(38, 18), nullable=False),
        sa.Column("quote_volume", sa.Numeric(38, 18), nullable=False),
        sa.Column("trade_count", sa.BigInteger()),
        sa.Column("source_time", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("market_id", "timeframe", "open_time"),
    )
    op.create_index("ix_candles_market_time", "candles", ["market_id", "open_time"])
    op.create_table(
        "derivatives_observations",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("market_id", sa.BigInteger(), sa.ForeignKey("markets.id"), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open_interest", sa.Numeric(38, 18)),
        sa.Column("funding_rate", sa.Numeric(30, 18)),
        sa.Column("mark_price", sa.Numeric(38, 18)),
        sa.Column("index_price", sa.Numeric(38, 18)),
        sa.Column("basis_rate", sa.Numeric(30, 18)),
        sa.Column("source", sa.String(32), nullable=False),
    )
    op.create_index("ix_derivatives_market_time", "derivatives_observations", ["market_id", "observed_at"])
    op.create_table(
        "trades",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("market_id", sa.BigInteger(), sa.ForeignKey("markets.id"), nullable=False),
        sa.Column("trade_id", sa.String(128)),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price", sa.Numeric(38, 18), nullable=False),
        sa.Column("quantity", sa.Numeric(38, 18), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
    )
    op.create_index("ix_trades_market_time", "trades", ["market_id", "executed_at"])
    op.create_table(
        "signals",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("market_id", sa.BigInteger(), sa.ForeignKey("markets.id"), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("score", sa.Numeric(6, 3), nullable=False),
        sa.Column("data_quality", sa.Numeric(6, 3), nullable=False),
        sa.Column("strategy_version", sa.String(64), nullable=False),
        sa.Column("feature_version", sa.String(64), nullable=False),
        sa.Column("model_version", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_index("ix_signals_market_time", "signals", ["market_id", "generated_at"])


def downgrade() -> None:
    op.drop_index("ix_signals_market_time", table_name="signals")
    op.drop_table("signals")
    op.drop_index("ix_trades_market_time", table_name="trades")
    op.drop_table("trades")
    op.drop_index("ix_derivatives_market_time", table_name="derivatives_observations")
    op.drop_table("derivatives_observations")
    op.drop_index("ix_candles_market_time", table_name="candles")
    op.drop_table("candles")
    op.drop_table("markets")
    op.drop_table("assets")
