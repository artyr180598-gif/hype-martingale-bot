from datetime import datetime
from pandas import DataFrame
from freqtrade.strategy import IStrategy


class PrimeStrategy(IStrategy):
    INTERFACE_VERSION = 3
    can_short = True
    timeframe = "5m"
    startup_candle_count = 240
    process_only_new_candles = True

    minimal_roi = {"0": 0.03, "30": 0.015, "120": 0}
    stoploss = -0.02
    use_exit_signal = True

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        close = dataframe["close"]
        volume = dataframe["volume"]

        dataframe["ema_fast"] = close.ewm(span=21, adjust=False).mean()
        dataframe["ema_slow"] = close.ewm(span=55, adjust=False).mean()
        dataframe["ema_trend"] = close.ewm(span=200, adjust=False).mean()

        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, float("nan"))
        dataframe["rsi"] = 100 - (100 / (1 + rs))

        tr = dataframe[["high", "low", "close"]].copy()
        tr["hl"] = dataframe["high"] - dataframe["low"]
        tr["hc"] = (dataframe["high"] - dataframe["close"].shift()).abs()
        tr["lc"] = (dataframe["low"] - dataframe["close"].shift()).abs()
        dataframe["atr"] = tr[["hl", "hc", "lc"]].max(axis=1).rolling(14).mean()

        dataframe["volume_ma"] = volume.rolling(30).mean()
        dataframe["volume_ratio"] = volume / dataframe["volume_ma"].replace(0, float("nan"))

        dataframe["trend_strength"] = (
            (dataframe["ema_fast"] - dataframe["ema_slow"]).abs()
            / close.replace(0, float("nan"))
        )
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        valid = dataframe["volume"] > 0
        volume_ok = dataframe["volume_ratio"] > 1.20
        trend_ok = dataframe["trend_strength"] > 0.0015

        long = (
            valid
            & volume_ok
            & trend_ok
            & (dataframe["ema_fast"] > dataframe["ema_slow"])
            & (dataframe["ema_slow"] > dataframe["ema_trend"])
            & (dataframe["rsi"] > 50)
            & (dataframe["rsi"] < 72)
            & (dataframe["close"] > dataframe["ema_fast"])
        )

        short = (
            valid
            & volume_ok
            & trend_ok
            & (dataframe["ema_fast"] < dataframe["ema_slow"])
            & (dataframe["ema_slow"] < dataframe["ema_trend"])
            & (dataframe["rsi"] < 50)
            & (dataframe["rsi"] > 28)
            & (dataframe["close"] < dataframe["ema_fast"])
        )

        dataframe.loc[long, ["enter_long", "enter_tag"]] = (1, "prime_volume_trend")
        dataframe.loc[short, ["enter_short", "enter_tag"]] = (1, "prime_volume_trend")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                (dataframe["ema_fast"] < dataframe["ema_slow"])
                | (dataframe["rsi"] > 78)
            ),
            "exit_long",
        ] = 1

        dataframe.loc[
            (
                (dataframe["ema_fast"] > dataframe["ema_slow"])
                | (dataframe["rsi"] < 22)
            ),
            "exit_short",
        ] = 1
        return dataframe

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
        return min(3.0, max_leverage)
