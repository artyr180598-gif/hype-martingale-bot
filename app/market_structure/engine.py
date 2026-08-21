from dataclasses import dataclass
from enum import StrEnum


class StructureBias(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


@dataclass(frozen=True, slots=True)
class StructureState:
    bias: StructureBias
    last_swing_high: float | None
    last_swing_low: float | None
    bos: bool
    choch: bool


def detect_structure(highs: list[float], lows: list[float], closes: list[float], lookback: int = 3) -> StructureState:
    if len(highs) != len(lows) or len(lows) != len(closes) or len(closes) < lookback * 2 + 1:
        return StructureState(StructureBias.NEUTRAL, None, None, False, False)
    swing_highs: list[float] = []
    swing_lows: list[float] = []
    for i in range(lookback, len(closes) - lookback):
        if highs[i] == max(highs[i - lookback:i + lookback + 1]):
            swing_highs.append(highs[i])
        if lows[i] == min(lows[i - lookback:i + lookback + 1]):
            swing_lows.append(lows[i])
    last_high = swing_highs[-1] if swing_highs else None
    last_low = swing_lows[-1] if swing_lows else None
    close = closes[-1]
    bos_up = last_high is not None and close > last_high
    bos_down = last_low is not None and close < last_low
    if bos_up:
        bias = StructureBias.BULLISH
    elif bos_down:
        bias = StructureBias.BEARISH
    elif len(swing_highs) >= 2 and len(swing_lows) >= 2:
        bias = StructureBias.BULLISH if swing_highs[-1] > swing_highs[-2] and swing_lows[-1] > swing_lows[-2] else StructureBias.BEARISH if swing_highs[-1] < swing_highs[-2] and swing_lows[-1] < swing_lows[-2] else StructureBias.NEUTRAL
    else:
        bias = StructureBias.NEUTRAL
    return StructureState(bias, last_high, last_low, bos_up or bos_down, False)
