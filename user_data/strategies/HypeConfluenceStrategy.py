from datetime import datetime
from typing import Optional

from pandas import DataFrame
from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy, informative


class HypeConfluenceStrategy(IStrategy):
    """
    Rebuilt Hype engine.

    Design sources:
      - Freqtrade/NFI: multi-timeframe confluence, volume universe, explicit exits.
      - Hummingbot: executor-style separation of entry / DCA / risk barriers.
      - SMC-style systems: market structure, BOS/CHOCH, FVG and order-block proxies.

    Important:
      - "score" is a ranking score, NOT a probability.
      - No claim of profitability is made without backtest + dry-run evidence.
      - Recovery is conditional DCA, never blind loss-doubling.
    """

    INTERFACE_VERSION = 3
    can_short = True

    timeframe = "5m"
    startup_candle_count = 400
    process_only_new_candles = True

    minimal_roi = {"0": 0.030, "45": 0.018, "120": 0.0}
    stoploss = -0.055
    use_exit_signal = True

    position_adjustment_enable = True
    max_entry_position_adjustment = 2

    INITIAL_STAKE = 50.0
    RECOVERY_STAKES = (50.0, 75.0)
    MAX_POSITION_STAKE = 175.0

    @informative("15m")
    def populate_indicators_15m(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        close = dataframe["close"]
        dataframe["ema_fast"] = close.ewm(span=21, adjust=False).mean()
        dataframe["ema_slow"] = close.ewm(span=55, adjust=False).mean()
        dataframe["ema_trend"] = close.ewm(span=200, adjust=False).mean()
        dataframe["rsi"] = self._rsi(close, 14)
        return dataframe

    @informative("1h")
    def populate_indicators_1h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        close = dataframe["close"]
        dataframe["ema_fast"] = close.ewm(span=21, adjust=False).mean()
        dataframe["ema_slow"] = close.ewm(span=55, adjust=False).mean()
        dataframe["ema_trend"] = close.ewm(span=200, adjust=False).mean()
        dataframe["rsi"] = self._rsi(close, 14)
        return dataframe

    @staticmethod
    def _rsi(close, period: int = 14):
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gain / loss.replace(0, float("nan"))
        return 100 - (100 / (1 + rs))

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        close = dataframe["close"]
        high = dataframe["high"]
        low = dataframe["low"]
        open_ = dataframe["open"]
        volume = dataframe["volume"]

        # Trend / momentum.
        dataframe["ema21"] = close.ewm(span=21, adjust=False).mean()
        dataframe["ema55"] = close.ewm(span=55, adjust=False).mean()
        dataframe["ema200"] = close.ewm(span=200, adjust=False).mean()
        dataframe["rsi"] = self._rsi(close, 14)

        prev_close = close.shift(1)
        tr = DataFrame(index=dataframe.index)
        tr["hl"] = high - low
        tr["hc"] = (high - prev_close).abs()
        tr["lc"] = (low - prev_close).abs()
        dataframe["atr"] = tr.max(axis=1).rolling(14).mean()

        # Participation.
        dataframe["vol_ma"] = volume.rolling(30).mean()
        dataframe["vol_ratio"] = volume / dataframe["vol_ma"].replace(0, float("nan"))
        candle_range = (high - low).replace(0, float("nan"))
        dataframe["body_ratio"] = (close - open_) / candle_range
        dataframe["flow"] = dataframe["body_ratio"] * dataframe["vol_ratio"]

        # VWAP-like rolling fair value.
        typical = (high + low + close) / 3.0
        dataframe["vwap48"] = (
            (typical * volume).rolling(48).sum()
            / volume.rolling(48).sum().replace(0, float("nan"))
        )

        # Confirmed fractal structure. The pivot is 2 candles old, so it is
        # known without looking into future candles.
        pivot_hi = (
            (high.shift(2) > high.shift(3))
            & (high.shift(2) > high.shift(1))
            & (high.shift(2) > high.shift(4))
            & (high.shift(2) > high)
        )
        pivot_lo = (
            (low.shift(2) < low.shift(3))
            & (low.shift(2) < low.shift(1))
            & (low.shift(2) < low.shift(4))
            & (low.shift(2) < low)
        )
        dataframe["last_swing_high"] = high.shift(2).where(pivot_hi).ffill()
        dataframe["last_swing_low"] = low.shift(2).where(pivot_lo).ffill()

        # Break of structure / change of character.
        dataframe["bos_long"] = close > dataframe["last_swing_high"].shift(1)
        dataframe["bos_short"] = close < dataframe["last_swing_low"].shift(1)

        trend_up = (dataframe["ema21"] > dataframe["ema55"]) & (dataframe["ema55"] > dataframe["ema200"])
        trend_down = (dataframe["ema21"] < dataframe["ema55"]) & (dataframe["ema55"] < dataframe["ema200"])
        dataframe["choch_long"] = dataframe["bos_long"] & ~trend_up.shift(1).fillna(False)
        dataframe["choch_short"] = dataframe["bos_short"] & ~trend_down.shift(1).fillna(False)

        # Fair-value-gap proxy: current candle leaves a 2-candle gap.
        dataframe["fvg_long"] = low > high.shift(2)
        dataframe["fvg_short"] = high < low.shift(2)

        # Order-block proxy: last opposite candle immediately before a BOS.
        dataframe["ob_long"] = (close.shift(1) < open_.shift(1)) & dataframe["bos_long"]
        dataframe["ob_short"] = (close.shift(1) > open_.shift(1)) & dataframe["bos_short"]
        dataframe["ob_long_mid"] = ((open_.shift(1) + close.shift(1)) / 2).where(dataframe["ob_long"]).ffill()
        dataframe["ob_short_mid"] = ((open_.shift(1) + close.shift(1)) / 2).where(dataframe["ob_short"]).ffill()

        # Volatility / extension filters.
        dataframe["atr_pct"] = dataframe["atr"] / close.replace(0, float("nan"))
        dataframe["extension_atr"] = (close - dataframe["ema21"]).abs() / dataframe["atr"].replace(0, float("nan"))
        dataframe["vwap_side_long"] = close > dataframe["vwap48"]
        dataframe["vwap_side_short"] = close < dataframe["vwap48"]

        # Higher timeframe regime supplied by Freqtrade's informative-pair merge.
        dataframe["htf15_long"] = (
            (dataframe.get("ema_fast_15m", close) > dataframe.get("ema_slow_15m", close))
            & (dataframe.get("ema_slow_15m", close) > dataframe.get("ema_trend_15m", close))
        )
        dataframe["htf15_short"] = (
            (dataframe.get("ema_fast_15m", close) < dataframe.get("ema_slow_15m", close))
            & (dataframe.get("ema_slow_15m", close) < dataframe.get("ema_trend_15m", close))
        )
        dataframe["htf1h_long"] = (
            (dataframe.get("ema_fast_1h", close) > dataframe.get("ema_slow_1h", close))
            & (dataframe.get("ema_slow_1h", close) > dataframe.get("ema_trend_1h", close))
        )
        dataframe["htf1h_short"] = (
            (dataframe.get("ema_fast_1h", close) < dataframe.get("ema_slow_1h", close))
            & (dataframe.get("ema_slow_1h", close) < dataframe.get("ema_trend_1h", close))
        )

        # Regime gate: score is deliberately not presented as probability.
        long_score = (
            18 * trend_up.astype(int)
            + 12 * dataframe["htf15_long"].astype(int)
            + 12 * dataframe["htf1h_long"].astype(int)
            + 12 * (dataframe["rsi"].between(50, 68)).astype(int)
            + 10 * (dataframe["vol_ratio"] >= 1.15).astype(int)
            + 10 * (dataframe["flow"] > 0.10).astype(int)
            + 10 * (dataframe["bos_long"] | dataframe["choch_long"]).astype(int)
            + 8 * (dataframe["fvg_long"] | dataframe["ob_long"]).astype(int)
            + 8 * dataframe["vwap_side_long"].astype(int)
        )
        short_score = (
            18 * trend_down.astype(int)
            + 12 * dataframe["htf15_short"].astype(int)
            + 12 * dataframe["htf1h_short"].astype(int)
            + 12 * (dataframe["rsi"].between(32, 50)).astype(int)
            + 10 * (dataframe["vol_ratio"] >= 1.15).astype(int)
            + 10 * (dataframe["flow"] < -0.10).astype(int)
            + 10 * (dataframe["bos_short"] | dataframe["choch_short"]).astype(int)
            + 8 * (dataframe["fvg_short"] | dataframe["ob_short"]).astype(int)
            + 8 * dataframe["vwap_side_short"].astype(int)
        )

        dataframe["long_score"] = long_score
        dataframe["short_score"] = short_score

        # Entry distance guard: don't chase candles that are already stretched.
        dataframe["entry_ok"] = dataframe["extension_atr"] < 1.8

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        valid = (
            (dataframe["volume"] > 0)
            & dataframe["atr"].notna()
            & dataframe["rsi"].notna()
            & dataframe["entry_ok"]
        )

        long_trigger = dataframe["bos_long"] | dataframe["choch_long"] | dataframe["fvg_long"] | dataframe["ob_long"]
        short_trigger = dataframe["bos_short"] | dataframe["choch_short"] | dataframe["fvg_short"] | dataframe["ob_short"]

        long = (
            valid
            & (dataframe["long_score"] >= 72)
            & (dataframe["htf15_long"])
            & (dataframe["htf1h_long"])
            & long_trigger
            & (dataframe["flow"] > 0)
        )
        short = (
            valid
            & (dataframe["short_score"] >= 72)
            & (dataframe["htf15_short"])
            & (dataframe["htf1h_short"])
            & short_trigger
            & (dataframe["flow"] < 0)
        )

        dataframe.loc[long, ["enter_long", "enter_tag"]] = (1, "HYPE_CONFLUENCE_LONG")
        dataframe.loc[short, ["enter_short", "enter_tag"]] = (1, "HYPE_CONFLUENCE_SHORT")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        long_exit = (
            ((dataframe["ema21"] < dataframe["ema55"]) & (dataframe["flow"] < 0))
            | (dataframe["rsi"] > 76)
            | (dataframe["htf1h_short"])
        )
        short_exit = (
            ((dataframe["ema21"] > dataframe["ema55"]) & (dataframe["flow"] > 0))
            | (dataframe["rsi"] < 24)
            | (dataframe["htf1h_long"])
        )
        dataframe.loc[long_exit, "exit_long"] = 1
        dataframe.loc[short_exit, "exit_short"] = 1
        return dataframe

    def custom_stake_amount(
        self, pair: str, current_time: datetime, current_rate: float,
        proposed_stake: float, min_stake: Optional[float], max_stake: float,
        leverage: float, entry_tag: Optional[str], side: str, **kwargs
    ) -> float:
        return min(self.INITIAL_STAKE, proposed_stake, max_stake)

    def _adjustment_count(self, trade: Trade) -> int:
        # nr_of_successful_entries includes the first entry.
        return max(0, int(getattr(trade, "nr_of_successful_entries", 1)) - 1)

    def adjust_trade_position(
        self, trade: Trade, current_time: datetime, current_rate: float,
        current_profit: float, min_stake: Optional[float], max_stake: float,
        current_entry_rate: float, current_exit_rate: float,
        current_entry_profit: float, current_exit_profit: float, **kwargs
    ):
        step = self._adjustment_count(trade)
        if step >= len(self.RECOVERY_STAKES) or trade.has_open_orders:
            return None

        dataframe, _ = self.dp.get_analyzed_dataframe(trade.pair, self.timeframe)
        if dataframe is None or dataframe.empty:
            return None
        c = dataframe.iloc[-1]

        # Require actual adverse price movement before recovery.
        adverse = (current_rate / current_entry_rate - 1.0) * (-1.0 if trade.is_short else 1.0)
        threshold = 0.012 if step == 0 else 0.025
        if adverse > -threshold:
            return None

        # Recovery requires the original directional thesis to remain valid.
        directional_score = float(c["short_score"] if trade.is_short else c["long_score"])
        opposite_score = float(c["long_score"] if trade.is_short else c["short_score"])
        trend_ok = (
            bool(c["ema21"] < c["ema55"]) if trade.is_short else bool(c["ema21"] > c["ema55"])
        )
        htf_ok = (
            bool(c["htf1h_short"] and c["htf15_short"])
            if trade.is_short
            else bool(c["htf1h_long"] and c["htf15_long"])
        )
        flow_ok = float(c["flow"]) < 0 if trade.is_short else float(c["flow"]) > 0

        if directional_score < 68 or opposite_score > directional_score - 8 or not (trend_ok and htf_ok and flow_ok):
            return None

        # Never recover during extreme extension.
        if float(c["extension_atr"]) > 2.5:
            return None

        stake = min(
            self.RECOVERY_STAKES[step],
            max_stake,
            max(0.0, self.MAX_POSITION_STAKE - float(trade.stake_amount)),
        )
        if min_stake is not None and stake < min_stake:
            return None

        return stake, f"RECOVERY_{step + 1}"

    def custom_exit(
        self, pair: str, trade: Trade, current_time: datetime,
        current_rate: float, current_profit: float, **kwargs
    ):
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe is None or dataframe.empty:
            return None
        c = dataframe.iloc[-1]

        if trade.is_short:
            broken = bool(c["htf1h_long"] and c["ema21"] > c["ema55"] and c["flow"] > 0)
        else:
            broken = bool(c["htf1h_short"] and c["ema21"] < c["ema55"] and c["flow"] < 0)

        if broken:
            return "THESIS_BROKEN"
        return None

    def confirm_trade_entry(
        self, pair: str, order_type: str, amount: float, rate: float,
        time_in_force: str, current_time: datetime, entry_tag: Optional[str],
        side: str, **kwargs
    ) -> bool:
        # Live/dry-run order-book sanity gate. Backtests don't have a live book.
        try:
            if self.dp.runmode.value not in ("live", "dry_run"):
                return True
            ob = self.dp.orderbook(pair, 10)
            bids, asks = ob.get("bids", []), ob.get("asks", [])
            if not bids or not asks:
                return True
            bid = sum(float(p) * float(a) for p, a in bids)
            ask = sum(float(p) * float(a) for p, a in asks)
            total = bid + ask
            if total <= 0:
                return True
            imbalance = (bid - ask) / total
            if side == "long" and imbalance < -0.20:
                return False
            if side == "short" and imbalance > 0.20:
                return False
        except Exception:
            return True
        return True

    def leverage(
        self, pair: str, current_time: datetime, current_rate: float,
        proposed_leverage: float, max_leverage: float, entry_tag: Optional[str],
        side: str, **kwargs
    ) -> float:
        # 1x default; leverage is earned by confluence, never used to rescue a loss.
        lev = 1.0
        try:
            dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if dataframe is not None and not dataframe.empty:
                c = dataframe.iloc[-1]
                score = float(c["short_score"] if side == "short" else c["long_score"])
                if score >= 84:
                    lev = 2.0
                if score >= 92 and float(c["vol_ratio"]) >= 1.5:
                    lev = 3.0
        except Exception:
            lev = 1.0
        return min(3.0, max_leverage, lev)
