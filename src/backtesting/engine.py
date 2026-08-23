"""
Event-Driven Realistic Futures Backtesting Engine.
"""
from dataclasses import dataclass, field
from typing import Any

from src.backtesting.metrics import BacktestMetrics, MetricsCalculator
from src.config.constants import SignalDirection
from src.core.logging import get_logger
from src.data.models import CandleData
from src.features.pipeline import FeaturePipeline
from src.strategies.base import BaseStrategy

logger = get_logger("backtesting.engine")


@dataclass
class BacktestResult:
    backtest_id: str
    strategy_name: str
    symbol: str
    timeframe: str
    metrics: BacktestMetrics
    trades: list[dict[str, Any]]
    equity_curve: list[float]
    timestamps: list[int]
    regime_breakdown: dict[str, Any] = field(default_factory=dict)


class BacktestEngine:
    """
    Simulates high-fidelity perpetual futures execution with zero lookahead bias.
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        initial_balance: float = 10000.0,
        risk_per_trade_pct: float = 1.5,
        default_leverage: int = 5,
        taker_fee_pct: float = 0.05,
        slippage_pct: float = 0.05,
        funding_8h_pct: float = 0.01,
        mmr: float = 0.005,
    ):
        self.strategy = strategy
        self.initial_balance = initial_balance
        self.risk_per_trade_pct = risk_per_trade_pct
        self.default_leverage = default_leverage
        self.taker_fee_pct = taker_fee_pct
        self.slippage_pct = slippage_pct
        self.funding_8h_pct = funding_8h_pct
        self.mmr = mmr
        self.feature_pipeline = FeaturePipeline()

    def run(self, candles: list[CandleData]) -> BacktestResult:
        if len(candles) < 25:
            raise ValueError(f"Insufficient candles for backtest: {len(candles)} (minimum 25 required)")

        symbol = candles[0].symbol
        timeframe = candles[0].timeframe
        df = self.feature_pipeline.candles_to_dataframe(candles)

        balance = self.initial_balance
        equity_curve: list[float] = [balance]
        timestamps: list[int] = [int(candles[0].timestamp_ms)]
        trades: list[dict[str, Any]] = []

        # Position tracking
        in_trade = False
        pos_side = "LONG"
        pos_qty = 0.0
        entry_price = 0.0
        stop_loss = 0.0
        tp1 = 0.0
        tp2 = 0.0
        tp3 = 0.0
        initial_risk_usd = 0.0
        margin_locked = 0.0
        tp1_hit = False
        tp2_hit = False
        opened_at_ts = 0
        last_funding_block = 0
        total_fees = 0.0
        total_funding = 0.0

        # Step through bars chronologically
        warmup_period = 20
        for i in range(warmup_period, len(df)):
            sub_df = df.iloc[: i + 1]
            last_bar = sub_df.iloc[-1]
            ts = int(last_bar["timestamp_ms"])
            bar_open = float(last_bar["open"])
            bar_high = float(last_bar["high"])
            bar_low = float(last_bar["low"])
            bar_close = float(last_bar["close"])

            # 1. 8h Funding Rate deduction
            if in_trade:
                funding_block = ts // (8 * 3600 * 1000)
                if funding_block != last_funding_block:
                    last_funding_block = funding_block
                    funding_fee = (pos_qty * entry_price) * (self.funding_8h_pct / 100.0)
                    balance -= funding_fee
                    total_funding += funding_fee

            # 2. Check Exits if in trade
            if in_trade:
                # A. Liquidation check
                if pos_side == "LONG":
                    liq_price = entry_price * (1.0 - (1.0 / self.default_leverage) + self.mmr)
                    if bar_low <= liq_price:
                        # Full liquidation
                        loss_usd = margin_locked
                        balance = max(0.0, balance - loss_usd)
                        trades.append({
                            "side": pos_side,
                            "entry_price": entry_price,
                            "exit_price": liq_price,
                            "net_pnl": -loss_usd,
                            "realized_r": -1.0,
                            "is_liquidation": True,
                            "reason": "LIQUIDATION",
                            "opened_ts": opened_at_ts,
                            "closed_ts": ts,
                        })
                        in_trade = False
                        pos_qty = margin_locked = 0.0
                        equity_curve.append(balance)
                        timestamps.append(ts)
                        continue

                elif pos_side == "SHORT":
                    liq_price = entry_price * (1.0 + (1.0 / self.default_leverage) - self.mmr)
                    if bar_high >= liq_price:
                        loss_usd = margin_locked
                        balance = max(0.0, balance - loss_usd)
                        trades.append({
                            "side": pos_side,
                            "entry_price": entry_price,
                            "exit_price": liq_price,
                            "net_pnl": -loss_usd,
                            "realized_r": -1.0,
                            "is_liquidation": True,
                            "reason": "LIQUIDATION",
                            "opened_ts": opened_at_ts,
                            "closed_ts": ts,
                        })
                        in_trade = False
                        pos_qty = margin_locked = 0.0
                        equity_curve.append(balance)
                        timestamps.append(ts)
                        continue

                # B. Stop Loss Hit
                if (pos_side == "LONG" and bar_low <= stop_loss) or (pos_side == "SHORT" and bar_high >= stop_loss):
                    exit_p = stop_loss * (1.0 - (self.slippage_pct / 100.0) if pos_side == "LONG" else 1.0 + (self.slippage_pct / 100.0))
                    gross_pnl = (exit_p - entry_price) * pos_qty if pos_side == "LONG" else (entry_price - exit_p) * pos_qty
                    exit_fee = pos_qty * exit_p * (self.taker_fee_pct / 100.0)
                    net_pnl = gross_pnl - exit_fee
                    balance += net_pnl
                    r_multiple = net_pnl / initial_risk_usd if initial_risk_usd > 0 else -1.0

                    trades.append({
                        "side": pos_side,
                        "entry_price": entry_price,
                        "exit_price": exit_p,
                        "net_pnl": round(net_pnl, 2),
                        "realized_r": round(r_multiple, 2),
                        "is_liquidation": False,
                        "reason": "STOP_LOSS" if not tp1_hit else "BREAKEVEN_SL",
                        "opened_ts": opened_at_ts,
                        "closed_ts": ts,
                    })
                    in_trade = False
                    pos_qty = margin_locked = 0.0

                # C. Partial Take Profit Scaling
                elif pos_side == "LONG":
                    if not tp1_hit and bar_high >= tp1:
                        tp1_hit = True
                        close_qty = pos_qty * 0.40
                        pnl = close_qty * (tp1 - entry_price) - (close_qty * tp1 * self.taker_fee_pct / 100.0)
                        balance += pnl
                        pos_qty -= close_qty
                        stop_loss = entry_price  # Move SL to breakeven

                    if tp1_hit and not tp2_hit and tp2 > 0 and bar_high >= tp2:
                        tp2_hit = True
                        close_qty = pos_qty * 0.60
                        pnl = close_qty * (tp2 - entry_price) - (close_qty * tp2 * self.taker_fee_pct / 100.0)
                        balance += pnl
                        pos_qty -= close_qty

                    if tp2_hit and tp3 > 0 and bar_high >= tp3:
                        pnl = pos_qty * (tp3 - entry_price) - (pos_qty * tp3 * self.taker_fee_pct / 100.0)
                        balance += pnl
                        trades.append({
                            "side": pos_side,
                            "entry_price": entry_price,
                            "exit_price": tp3,
                            "net_pnl": round(pnl, 2),
                            "realized_r": 3.0,
                            "is_liquidation": False,
                            "reason": "FULL_TP_REACHED",
                            "opened_ts": opened_at_ts,
                            "closed_ts": ts,
                        })
                        in_trade = False
                        pos_qty = margin_locked = 0.0

                elif pos_side == "SHORT":
                    if not tp1_hit and bar_low <= tp1:
                        tp1_hit = True
                        close_qty = pos_qty * 0.40
                        pnl = close_qty * (entry_price - tp1) - (close_qty * tp1 * self.taker_fee_pct / 100.0)
                        balance += pnl
                        pos_qty -= close_qty
                        stop_loss = entry_price  # Move SL to breakeven

                    if tp1_hit and not tp2_hit and tp2 > 0 and bar_low <= tp2:
                        tp2_hit = True
                        close_qty = pos_qty * 0.60
                        pnl = close_qty * (entry_price - tp2) - (close_qty * tp2 * self.taker_fee_pct / 100.0)
                        balance += pnl
                        pos_qty -= close_qty

                    if tp2_hit and tp3 > 0 and bar_low <= tp3:
                        pnl = pos_qty * (entry_price - tp3) - (pos_qty * tp3 * self.taker_fee_pct / 100.0)
                        balance += pnl
                        trades.append({
                            "side": pos_side,
                            "entry_price": entry_price,
                            "exit_price": tp3,
                            "net_pnl": round(pnl, 2),
                            "realized_r": 3.0,
                            "is_liquidation": False,
                            "reason": "FULL_TP_REACHED",
                            "opened_ts": opened_at_ts,
                            "closed_ts": ts,
                        })
                        in_trade = False
                        pos_qty = margin_locked = 0.0

            # 3. Check New Entry Signal if not in trade
            if not in_trade and balance > 100.0:
                sub_candles = candles[: i + 1]
                feat_matrix = self.feature_pipeline.compute_feature_matrix(sub_candles)
                sig = self.strategy.evaluate(feat_matrix)

                if sig.direction in (SignalDirection.LONG, SignalDirection.SHORT) and sig.score >= 70.0:
                    pos_side = sig.direction.value
                    fill_p = (
                        bar_close * (1.0 + self.slippage_pct / 100.0)
                        if pos_side == "LONG"
                        else bar_close * (1.0 - self.slippage_pct / 100.0)
                    )
                    entry_price = fill_p
                    stop_loss = sig.stop_loss
                    tp1 = sig.take_profit_1
                    tp2 = sig.take_profit_2 or (entry_price + (entry_price - stop_loss) * 2.5)
                    tp3 = sig.take_profit_3 or (entry_price + (entry_price - stop_loss) * 4.0)

                    # Position Sizing
                    risk_usd = balance * (self.risk_per_trade_pct / 100.0)
                    stop_dist = abs(entry_price - stop_loss)
                    if stop_dist > 0:
                        pos_qty = risk_usd / stop_dist
                        notional = pos_qty * entry_price
                        margin_locked = notional / self.default_leverage
                        entry_fee = notional * (self.taker_fee_pct / 100.0)

                        if margin_locked + entry_fee <= balance:
                            balance -= entry_fee
                            total_fees += entry_fee
                            initial_risk_usd = risk_usd
                            in_trade = True
                            tp1_hit = False
                            tp2_hit = False
                            opened_at_ts = ts

            # Mark to market equity
            unrealized_pnl = 0.0
            if in_trade:
                unrealized_pnl = (bar_close - entry_price) * pos_qty if pos_side == "LONG" else (entry_price - bar_close) * pos_qty

            cur_equity = balance + unrealized_pnl
            equity_curve.append(cur_equity)
            timestamps.append(ts)

        # Close any open trade at final candle
        if in_trade:
            final_p = float(df["close"].iloc[-1])
            pnl = (final_p - entry_price) * pos_qty if pos_side == "LONG" else (entry_price - final_p) * pos_qty
            balance += pnl
            trades.append({
                "side": pos_side,
                "entry_price": entry_price,
                "exit_price": final_p,
                "net_pnl": round(pnl, 2),
                "realized_r": round(pnl / initial_risk_usd, 2) if initial_risk_usd > 0 else 0.0,
                "is_liquidation": False,
                "reason": "END_OF_TEST_CLOSE",
                "opened_ts": opened_at_ts,
                "closed_ts": int(df["timestamp_ms"].iloc[-1]),
            })

        duration_days = (candles[-1].timestamp_ms - candles[0].timestamp_ms) / (86400.0 * 1000.0)
        metrics = MetricsCalculator.compute_metrics(
            trades=trades,
            equity_curve=equity_curve,
            initial_balance=self.initial_balance,
            duration_days=duration_days,
        )

        return BacktestResult(
            backtest_id=f"BT-{symbol}-{self.strategy.name}-{int(timestamps[0]/1000)}",
            strategy_name=self.strategy.name,
            symbol=symbol,
            timeframe=timeframe,
            metrics=metrics,
            trades=trades,
            equity_curve=equity_curve,
            timestamps=timestamps,
        )
