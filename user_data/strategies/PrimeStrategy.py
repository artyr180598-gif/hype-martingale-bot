from datetime import datetime
from pandas import DataFrame
from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy


class PrimeStrategy(IStrategy):
    """
    Hype / Prime strategy:
    - Multi-horizon market structure from the 5m stream.
    - Volume + candle-flow proxy on every candle.
    - Optional real public-trade orderflow (delta / imbalance) when enabled.
    - Early-entry scoring instead of a single late confirmation.
    - Controlled recovery (2 steps) only while the original thesis is intact.
    - Hard limits: 2 recovery entries, bounded exposure, max 3x leverage.
    """

    INTERFACE_VERSION = 3
    can_short = True
    timeframe = "5m"
    startup_candle_count = 300
    process_only_new_candles = True

    position_adjustment_enable = True
    max_entry_position_adjustment = 2

    # Conservative recovery sizes: 1.4x then 1.8x the initial stake.
    # This is deliberately NOT blind 2x martingale.
    RECOVERY_STAKES = (70.0, 90.0)
    MAX_TOTAL_STAKE = 210.0

    minimal_roi = {"0": 0.035, "45": 0.018, "150": 0}
    stoploss = -0.075
    use_exit_signal = True

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        close = dataframe["close"]
        high = dataframe["high"]
        low = dataframe["low"]
        volume = dataframe["volume"]

        # Core trend structure.
        dataframe["ema_fast"] = close.ewm(span=21, adjust=False).mean()
        dataframe["ema_slow"] = close.ewm(span=55, adjust=False).mean()
        dataframe["ema_trend"] = close.ewm(span=200, adjust=False).mean()

        # Multi-horizon structure on the 5m stream:
        # 12 ~= 1h, 48 ~= 4h, 240 ~= 20h.
        dataframe["ema_1h"] = close.ewm(span=12, adjust=False).mean()
        dataframe["ema_4h"] = close.ewm(span=48, adjust=False).mean()
        dataframe["ema_20h"] = close.ewm(span=240, adjust=False).mean()

        # RSI.
        delta_price = close.diff()
        gain = delta_price.clip(lower=0).rolling(14).mean()
        loss = (-delta_price.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, float("nan"))
        dataframe["rsi"] = 100 - (100 / (1 + rs))

        # ATR.
        prev_close = close.shift(1)
        tr = DataFrame(index=dataframe.index)
        tr["hl"] = high - low
        tr["hc"] = (high - prev_close).abs()
        tr["lc"] = (low - prev_close).abs()
        dataframe["atr"] = tr.max(axis=1).rolling(14).mean()

        # Volume / participation.
        dataframe["volume_ma"] = volume.rolling(30).mean()
        dataframe["volume_ratio"] = volume / dataframe["volume_ma"].replace(0, float("nan"))

        # Candle-flow proxy: useful even when public trade data is unavailable.
        candle_range = (high - low).replace(0, float("nan"))
        dataframe["body_ratio"] = (close - dataframe["open"]) / candle_range
        dataframe["flow_proxy"] = dataframe["body_ratio"] * volume
        dataframe["flow_proxy_ma"] = dataframe["flow_proxy"].rolling(12).mean()
        dataframe["flow_proxy_ratio"] = (
            dataframe["flow_proxy_ma"] / dataframe["volume_ma"].replace(0, float("nan"))
        )

        # Real public-trade orderflow is supplied by Freqtrade when enabled.
        if "delta" in dataframe.columns:
            dataframe["flow_delta"] = dataframe["delta"].fillna(0.0)
            dataframe["flow_ratio"] = (
                dataframe["flow_delta"]
                / volume.replace(0, float("nan"))
            ).fillna(0.0)
        else:
            dataframe["flow_delta"] = 0.0
            dataframe["flow_ratio"] = dataframe["flow_proxy_ratio"].fillna(0.0)

        if "total_trades" in dataframe.columns:
            dataframe["trade_count_ratio"] = (
                dataframe["total_trades"]
                / dataframe["total_trades"].rolling(30).mean().replace(0, float("nan"))
            ).fillna(1.0)
        else:
            dataframe["trade_count_ratio"] = 1.0

        # Trend strength and slopes.
        dataframe["trend_strength"] = (
            (dataframe["ema_fast"] - dataframe["ema_slow"]).abs()
            / close.replace(0, float("nan"))
        )
        dataframe["ema_4h_slope"] = dataframe["ema_4h"] / dataframe["ema_4h"].shift(12) - 1.0
        dataframe["ema_20h_slope"] = dataframe["ema_20h"] / dataframe["ema_20h"].shift(24) - 1.0

        # Market structure.
        dataframe["recent_high"] = high.shift(1).rolling(20).max()
        dataframe["recent_low"] = low.shift(1).rolling(20).min()
        dataframe["breakout_long"] = close > dataframe["recent_high"]
        dataframe["breakout_short"] = close < dataframe["recent_low"]

        dataframe["reclaim_long"] = (
            (close > dataframe["ema_fast"])
            & (close.shift(1) <= dataframe["ema_fast"].shift(1))
        )
        dataframe["reclaim_short"] = (
            (close < dataframe["ema_fast"])
            & (close.shift(1) >= dataframe["ema_fast"].shift(1))
        )

        dataframe["atr_distance"] = (
            (close - dataframe["ema_fast"]).abs()
            / dataframe["atr"].replace(0, float("nan"))
        )

        # Large-participation impulse proxy.
        dataframe["large_flow_long"] = (
            (dataframe["volume_ratio"] >= 1.8)
            & (dataframe["body_ratio"] >= 0.45)
            & (dataframe["flow_ratio"] >= 0.08)
        )
        dataframe["large_flow_short"] = (
            (dataframe["volume_ratio"] >= 1.8)
            & (dataframe["body_ratio"] <= -0.45)
            & (dataframe["flow_ratio"] <= -0.08)
        )

        # 0-100 score. This is a ranking/filter score, not a probability.
        long_score = (
            20 * (
                (dataframe["ema_fast"] > dataframe["ema_slow"])
                & (dataframe["ema_slow"] > dataframe["ema_trend"])
            ).astype(int)
            + 10 * (dataframe["ema_4h_slope"] > 0).astype(int)
            + 10 * (dataframe["ema_20h_slope"] > 0).astype(int)
            + 15 * (dataframe["flow_ratio"] > 0.05).astype(int)
            + 10 * (dataframe["volume_ratio"] > 1.20).astype(int)
            + 10 * (
                dataframe["breakout_long"] | dataframe["reclaim_long"]
            ).astype(int)
            + 10 * dataframe["rsi"].between(52, 70).astype(int)
            + 10 * (dataframe["atr_distance"] < 1.25).astype(int)
            + 5 * (dataframe["trade_count_ratio"] > 1.10).astype(int)
        )

        short_score = (
            20 * (
                (dataframe["ema_fast"] < dataframe["ema_slow"])
                & (dataframe["ema_slow"] < dataframe["ema_trend"])
            ).astype(int)
            + 10 * (dataframe["ema_4h_slope"] < 0).astype(int)
            + 10 * (dataframe["ema_20h_slope"] < 0).astype(int)
            + 15 * (dataframe["flow_ratio"] < -0.05).astype(int)
            + 10 * (dataframe["volume_ratio"] > 1.20).astype(int)
            + 10 * (
                dataframe["breakout_short"] | dataframe["reclaim_short"]
            ).astype(int)
            + 10 * dataframe["rsi"].between(30, 48).astype(int)
            + 10 * (dataframe["atr_distance"] < 1.25).astype(int)
            + 5 * (dataframe["trade_count_ratio"] > 1.10).astype(int)
        )

        dataframe["long_score"] = long_score
        dataframe["short_score"] = short_score
        dataframe["setup_score"] = long_score.where(long_score >= short_score, short_score)

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        valid = (
            (dataframe["volume"] > 0)
            & dataframe["atr"].notna()
            & dataframe["rsi"].notna()
        )

        long_trend = (
            (dataframe["ema_fast"] > dataframe["ema_slow"])
            & (dataframe["ema_slow"] > dataframe["ema_trend"])
            & (dataframe["ema_4h_slope"] > 0)
        )
        short_trend = (
            (dataframe["ema_fast"] < dataframe["ema_slow"])
            & (dataframe["ema_slow"] < dataframe["ema_trend"])
            & (dataframe["ema_4h_slope"] < 0)
        )

        long_flow = (
            (dataframe["flow_ratio"] > 0.05)
            | dataframe["large_flow_long"]
        )
        short_flow = (
            (dataframe["flow_ratio"] < -0.05)
            | dataframe["large_flow_short"]
        )

        long_trigger = dataframe["breakout_long"] | dataframe["reclaim_long"]
        short_trigger = dataframe["breakout_short"] | dataframe["reclaim_short"]

        long = (
            valid
            & long_trend
            & long_flow
            & long_trigger
            & (dataframe["long_score"] >= 72)
            & (dataframe["atr_distance"] < 1.25)
        )

        short = (
            valid
            & short_trend
            & short_flow
            & short_trigger
            & (dataframe["short_score"] >= 72)
            & (dataframe["atr_distance"] < 1.25)
        )

        dataframe.loc[long, ["enter_long", "enter_tag"]] = (
            1,
            "prime_early_flow_score",
        )
        dataframe.loc[short, ["enter_short", "enter_tag"]] = (
            1,
            "prime_early_flow_score",
        )
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                (
                    (dataframe["ema_fast"] < dataframe["ema_slow"])
                    & (dataframe["flow_ratio"] < 0)
                )
                | (dataframe["rsi"] > 78)
            ),
            "exit_long",
        ] = 1

        dataframe.loc[
            (
                (
                    (dataframe["ema_fast"] > dataframe["ema_slow"])
                    & (dataframe["flow_ratio"] > 0)
                )
                | (dataframe["rsi"] < 22)
            ),
            "exit_short",
        ] = 1
        return dataframe

    def custom_stake_amount(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_stake: float,
        min_stake: float | None,
        max_stake: float,
        leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        # Keep the configured initial stake. Recovery budget is bounded separately.
        return min(proposed_stake, 50.0, max_stake)

    def _recovery_state(self, trade: Trade) -> int:
        # First entry is nr_of_successful_entries == 1.
        return max(0, int(getattr(trade, "nr_of_successful_entries", 1)) - 1)

    def adjust_trade_position(
        self,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        min_stake: float | None,
        max_stake: float,
        current_entry_rate: float,
        current_exit_rate: float,
        current_entry_profit: float,
        current_exit_profit: float,
        **kwargs,
    ):
        if trade.has_open_orders:
            return None

        step = self._recovery_state(trade)
        if step >= len(self.RECOVERY_STAKES):
            return None

        dataframe, _ = self.dp.get_analyzed_dataframe(
            pair=trade.pair, timeframe=self.timeframe
        )
        if dataframe is None or dataframe.empty:
            return None

        candle = dataframe.iloc[-1]

        # Recovery is based on price movement, not leveraged PnL.
        direction = -1.0 if trade.is_short else 1.0
        price_move = direction * (current_rate / current_entry_rate - 1.0)

        # Require meaningful adverse movement.
        required_move = (0.008, 0.016)[step]
        if price_move > -required_move:
            return None

        # Never average into a broken trend.
        trend_ok = (
            bool(candle["ema_fast"] > candle["ema_slow"])
            if not trade.is_short
            else bool(candle["ema_fast"] < candle["ema_slow"])
        )
        higher_tf_ok = (
            bool(candle["ema_4h_slope"] > 0)
            if not trade.is_short
            else bool(candle["ema_4h_slope"] < 0)
        )
        flow_ok = (
            float(candle["flow_ratio"]) > 0.02
            if not trade.is_short
            else float(candle["flow_ratio"]) < -0.02
        )
        rsi_ok = (
            35 <= float(candle["rsi"]) <= 62
            if not trade.is_short
            else 38 <= float(candle["rsi"]) <= 65
        )

        # At least 3 of 4 thesis checks must still agree.
        confirmations = sum((trend_ok, higher_tf_ok, flow_ok, rsi_ok))
        if confirmations < 3:
            return None

        # Extra protection against averaging into an abnormal volatility spike.
        if float(candle["atr_distance"]) > 2.0:
            return None

        stake = min(
            self.RECOVERY_STAKES[step],
            max_stake,
            max(0.0, self.MAX_TOTAL_STAKE - float(trade.stake_amount)),
        )
        if min_stake is not None and stake < min_stake:
            return None

        return stake, f"smart_recovery_{step + 1}"

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ):
        dataframe, _ = self.dp.get_analyzed_dataframe(pair=pair, timeframe=self.timeframe)
        if dataframe is None or dataframe.empty:
            return None

        candle = dataframe.iloc[-1]
        if trade.is_short:
            thesis_broken = (
                candle["ema_fast"] > candle["ema_slow"]
                and candle["flow_ratio"] > 0.03
            )
        else:
            thesis_broken = (
                candle["ema_fast"] < candle["ema_slow"]
                and candle["flow_ratio"] < -0.03
            )

        if thesis_broken and current_profit < 0:
            return "thesis_broken"

        return None

    def leverage(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_leverage: float,
        max_leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        # Default to 1x. Increase only when the live setup score is strong.
        leverage = 1.0
        try:
            dataframe, _ = self.dp.get_analyzed_dataframe(
                pair=pair, timeframe=self.timeframe
            )
            if dataframe is not None and not dataframe.empty:
                candle = dataframe.iloc[-1]
                score = float(
                    candle["long_score"] if side == "long" else candle["short_score"]
                )
                flow = float(candle["flow_ratio"])
                aligned_flow = flow >= 0.08 if side == "long" else flow <= -0.08

                if score >= 82:
                    leverage = 2.0
                if score >= 90 and aligned_flow:
                    leverage = 3.0
        except Exception:
            leverage = 1.0

        return min(leverage, 3.0, max_leverage)
