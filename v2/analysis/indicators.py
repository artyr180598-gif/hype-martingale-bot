"""
Индикаторы v2 на чистом numpy.

Почему numpy, а не pandas: расчёт идёт на массивах фиксированной длины
(300 свечей), pandas здесь даёт только накладные расходы и лишнюю зависимость
в горячем пути сканера. Формулы совпадают с классикой (Wilder RMA для
ATR/RSI/ADX), что проверено тестом v2/tests/test_indicators.py — он сравнивает
результаты с эталонной pandas-реализацией из v1 (src/data/indicators.py).

Все функции принимают numpy-массивы одинаковой длины и возвращают либо массив,
либо скаляр (``*_last``).
"""

from __future__ import annotations

import numpy as np

RETRACEMENTS = (0.236, 0.382, 0.5, 0.618, 0.786)
EXTENSIONS = (1.0, 1.272, 1.618, 2.0, 2.618)


# ═══════════════════════════════════════════════════════════════
#  БАЗА
# ═══════════════════════════════════════════════════════════════
def as_array(values) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if arr.ndim != 1:
        raise ValueError("ожидается одномерный массив")
    return arr


def sma(values, period: int) -> np.ndarray:
    arr = as_array(values)
    out = np.full(arr.shape, np.nan)
    if len(arr) < period or period <= 0:
        return out
    csum = np.cumsum(np.insert(arr, 0, 0.0))
    out[period - 1 :] = (csum[period:] - csum[:-period]) / period
    return out


def ema(values, period: int) -> np.ndarray:
    """EMA как в pandas ewm(span=period, adjust=False): y = a·x + (1-a)·y."""
    arr = as_array(values)
    alpha = 2.0 / (period + 1.0)
    return _ewm(arr, alpha, min_periods=period)


def wilder_rma(values, period: int) -> np.ndarray:
    """Сглаживание Уайлдера: ewm(alpha=1/period, adjust=False)."""
    arr = as_array(values)
    return _ewm(arr, 1.0 / period, min_periods=period)


def _ewm(arr: np.ndarray, alpha: float, min_periods: int) -> np.ndarray:
    """Рекурсивное EWМ. NaN во входных данных пропускаются (как ignore_na=False)."""
    n = len(arr)
    out = np.full(n, np.nan)
    if n == 0:
        return out
    prev = np.nan
    seen = 0
    for i in range(n):
        x = arr[i]
        if np.isnan(x):
            out[i] = prev
            continue
        prev = x if np.isnan(prev) else alpha * x + (1.0 - alpha) * prev
        seen += 1
        if seen >= min_periods:
            out[i] = prev
    return out


def true_range(high, low, close) -> np.ndarray:
    h, lo, c = as_array(high), as_array(low), as_array(close)
    prev_close = np.empty_like(c)
    prev_close[0] = np.nan
    prev_close[1:] = c[:-1]
    return np.nanmax(
        np.vstack([h - lo, np.abs(h - prev_close), np.abs(lo - prev_close)]), axis=0
    )


def atr(high, low, close, period: int = 14) -> np.ndarray:
    return np.abs(wilder_rma(true_range(high, low, close), period))


def atr_pct(high, low, close, period: int = 14) -> np.ndarray:
    """ATR в процентах от цены — так уровни сравниваются между монетами."""
    c = as_array(close)
    rng = atr(high, low, close, period)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(c > 0, rng / c * 100.0, np.nan)
    return out


def rsi(close, period: int = 14) -> np.ndarray:
    c = as_array(close)
    delta = np.diff(c, prepend=np.nan)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    gain[0] = np.nan
    loss[0] = np.nan
    avg_gain = wilder_rma(gain, period)
    avg_loss = wilder_rma(loss, period)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
        out = np.where(avg_loss == 0, 100.0, 100.0 - 100.0 / (1.0 + rs))
    return np.clip(np.nan_to_num(out, nan=50.0), 0.0, 100.0)


def macd(close, fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, np.ndarray]:
    line = ema(close, fast) - ema(close, slow)
    sig = _ewm(line, 2.0 / (signal + 1.0), min_periods=signal)
    return {"macd": line, "signal": sig, "hist": line - sig}


# ═══════════════════════════════════════════════════════════════
#  ТРЕНД
# ═══════════════════════════════════════════════════════════════
def adx(high, low, close, period: int = 14) -> dict[str, np.ndarray]:
    """ADX/DI+/DI− (Wilder). ADX ≥ 25 — тренд есть, < 20 — флэт."""
    h, lo = as_array(high), as_array(low)
    up_move = np.diff(h, prepend=np.nan)
    down_move = -np.diff(lo, prepend=np.nan)
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_dm[0] = np.nan
    minus_dm[0] = np.nan

    tr = wilder_rma(true_range(h, lo, close), period)
    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = 100.0 * wilder_rma(plus_dm, period) / tr
        minus_di = 100.0 * wilder_rma(minus_dm, period) / tr
        di_sum = plus_di + minus_di
        dx = 100.0 * np.abs(plus_di - minus_di) / di_sum
    adx_val = np.clip(np.nan_to_num(wilder_rma(dx, period), nan=0.0), 0.0, 100.0)
    return {
        "adx": adx_val,
        "plus_di": np.nan_to_num(plus_di, nan=0.0).clip(min=0.0),
        "minus_di": np.nan_to_num(minus_di, nan=0.0).clip(min=0.0),
    }


def trend_strength(adx_value: float) -> str:
    if adx_value >= 40:
        return "strong"
    if adx_value >= 25:
        return "moderate"
    if adx_value >= 20:
        return "weak"
    return "none"


def trend_direction(plus_di: float, minus_di: float, adx_value: float) -> str:
    if adx_value < 20:
        return "flat"
    if plus_di > minus_di * 1.1:
        return "up"
    if minus_di > plus_di * 1.1:
        return "down"
    return "flat"


# ═══════════════════════════════════════════════════════════════
#  ОБЪЁМ
# ═══════════════════════════════════════════════════════════════
def obv(close, volume) -> np.ndarray:
    """On-Balance Volume: накопленный объём со знаком движения цены."""
    c, v = as_array(close), as_array(volume)
    direction = np.sign(np.diff(c, prepend=c[0] if len(c) else 0.0))
    return np.cumsum(direction * v)


def obv_slope(obv_series, period: int = 20) -> float:
    """
    Нормированный наклон OBV за period баров.

    Делим на средний абсолютный OBV, чтобы число было сравнимо между монетами:
    +0.3 означает «OBV вырос на 30% от своего среднего уровня» — это и есть
    признак накопления.
    """
    series = as_array(obv_series)
    if len(series) <= period:
        return 0.0
    window = series[-period:]
    x = np.arange(len(window), dtype=float)
    denom = np.nanmean(np.abs(window))
    if not np.isfinite(denom) or denom == 0:
        return 0.0
    slope = np.polyfit(x, window, 1)[0]
    return float(slope * period / denom)


def volume_zscore(volume, period: int = 20) -> np.ndarray:
    v = as_array(volume)
    out = np.full(v.shape, np.nan)
    if len(v) < period:
        return np.zeros_like(v)
    for i in range(period - 1, len(v)):
        window = v[i - period + 1 : i + 1]
        mean = window.mean()
        std = window.std()
        out[i] = 0.0 if std == 0 else (v[i] - mean) / std
    return np.nan_to_num(out, nan=0.0)


def vwap(high, low, close, volume) -> np.ndarray:
    """VWAP по всей выборке (без суточного сброса — для DEX-пулов сессий нет)."""
    h, lo, c, v = as_array(high), as_array(low), as_array(close), as_array(volume)
    typical = (h + lo + c) / 3.0
    cum_v = np.cumsum(v)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.cumsum(typical * v) / cum_v
    return np.nan_to_num(out, nan=typical)


# ═══════════════════════════════════════════════════════════════
#  СТРУКТУРА / ФИБОНАЧЧИ
# ═══════════════════════════════════════════════════════════════
def swing_points(high, low, left: int = 3, right: int = 3) -> tuple[list[int], list[int]]:
    """Локальные максимумы/минимумы (фракталы). Возвращает индексы."""
    h, lo = as_array(high), as_array(low)
    n = len(h)
    highs: list[int] = []
    lows: list[int] = []
    for i in range(left, n - right):
        window_h = h[i - left : i + right + 1]
        window_l = lo[i - left : i + right + 1]
        if h[i] == np.nanmax(window_h) and np.isfinite(h[i]):
            highs.append(i)
        if lo[i] == np.nanmin(window_l) and np.isfinite(lo[i]):
            lows.append(i)
    return highs, lows


def last_swing(high, low, direction: int = 1, left: int = 3, right: int = 3) -> float | None:
    """Последний значимый свинг: максимум для direction=1, минимум для -1."""
    highs, lows = swing_points(high, low, left, right)
    if direction > 0 and highs:
        return float(as_array(high)[highs[-1]])
    if direction < 0 and lows:
        return float(as_array(low)[lows[-1]])
    return None


def fib_levels(swing_low: float, swing_high: float, direction: int = 1) -> dict[str, dict[float, float]]:
    """
    Уровни Фибоначчи для свинга.

    direction=1 (тренд вверх): ретрейсменты считаются от вершины вниз,
    расширения — от основания вверх. Для direction=-1 зеркально.
    """
    span = swing_high - swing_low
    retracements = {
        r: (swing_high - span * r if direction > 0 else swing_low + span * r) for r in RETRACEMENTS
    }
    extensions = {
        e: (swing_low + span * e if direction > 0 else swing_high - span * e) for e in EXTENSIONS
    }
    return {"retracements": retracements, "extensions": extensions}


def compute_rr(entry: float, stop: float, target: float) -> float:
    """R:R всегда считается по модулям — знак направления не нужен."""
    risk = abs(entry - stop)
    reward = abs(target - entry)
    if risk <= 0:
        return 0.0
    return reward / risk


def last(series, default: float = 0.0) -> float:
    """Последнее конечное значение массива (или default, если всё NaN)."""
    arr = as_array(series)
    for value in arr[::-1]:
        if np.isfinite(value):
            return float(value)
    return default
