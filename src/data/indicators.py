"""
Библиотека технических индикаторов (чистый numpy/pandas, без TA-Lib).

Набор собран по образцу Jesse и Crypto-Signal: тренд (EMA/ADX/SuperTrend),
моментум (RSI/MACD/Stochastic/ROC), волатильность (ATR/Bollinger/Keltner),
объём (OBV/MFI/CVD/VWAP/z-score). Все функции векторизованы и не заглядывают
в будущее: значение в баре i зависит только от баров <= i.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────
#  БАЗОВЫЕ
# ─────────────────────────────────────────────────────────────


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def rma(series: pd.Series, period: int) -> pd.Series:
    """Сглаживание Уайлдера (используется в RSI/ATR/ADX)."""
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def roc(series: pd.Series, period: int) -> pd.Series:
    """Изменение в процентах за period баров."""
    prev = series.shift(period)
    return (series - prev) / prev * 100.0


def rolling_std(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).std(ddof=0)


# ─────────────────────────────────────────────────────────────
#  МОМЕНТУМ
# ─────────────────────────────────────────────────────────────


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = rma(gain, period)
    avg_loss = rma(loss, period)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    # нет потерь → RSI 100, нет движений → 50
    out = out.where(avg_loss != 0.0, 100.0)
    out = out.where(~((avg_gain == 0.0) & (avg_loss == 0.0)), 50.0)
    return out.clip(0.0, 100.0)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """
    MACD. Прогревочные бары заполнены нулём: это честное «сигнала ещё нет»
    и сохраняет тождество macd_hist == macd - macd_signal на всём фрейме.
    """
    macd_line = (ema(close, fast) - ema(close, slow)).fillna(0.0)
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean().fillna(0.0)
    hist = macd_line - signal_line
    return pd.DataFrame(
        {"macd": macd_line, "macd_signal": signal_line, "macd_hist": hist}, index=close.index
    )


def stochastic(high: pd.Series, low: pd.Series, close: pd.Series, k: int = 14, d: int = 3) -> pd.DataFrame:
    ll = low.rolling(k, min_periods=k).min()
    hh = high.rolling(k, min_periods=k).max()
    rng = (hh - ll).replace(0.0, np.nan)
    stoch_k = ((close - ll) / rng * 100.0).clip(0.0, 100.0)
    stoch_k = stoch_k.fillna(50.0)
    stoch_d = stoch_k.rolling(d, min_periods=d).mean()
    return pd.DataFrame({"stoch_k": stoch_k, "stoch_d": stoch_d}, index=close.index)


def williams_r(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    hh = high.rolling(period, min_periods=period).max()
    ll = low.rolling(period, min_periods=period).min()
    rng = (hh - ll).replace(0.0, np.nan)
    return (-100.0 * (hh - close) / rng).clip(-100.0, 0.0)


# ─────────────────────────────────────────────────────────────
#  ВОЛАТИЛЬНОСТЬ
# ─────────────────────────────────────────────────────────────


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    return pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    return rma(true_range(high, low, close), period).abs()


def bollinger(close: pd.Series, period: int = 20, mult: float = 2.0) -> pd.DataFrame:
    mid = sma(close, period)
    std = rolling_std(close, period)
    up = mid + mult * std
    low = mid - mult * std
    width = (up - low) / mid * 100.0
    band = (up - low).replace(0.0, np.nan)
    pctb = (close - low) / band
    return pd.DataFrame(
        {"bb_mid": mid, "bb_up": up, "bb_low": low, "bb_width": width, "bb_pctb": pctb},
        index=close.index,
    )


def keltner(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 20, mult: float = 1.5) -> pd.DataFrame:
    mid = ema(close, period)
    rng = atr(high, low, close, period)
    return pd.DataFrame(
        {"kc_mid": mid, "kc_up": mid + mult * rng, "kc_low": mid - mult * rng}, index=close.index
    )


def realized_vol(close: pd.Series, period: int = 20, bars_per_year: float = 35_040.0) -> pd.Series:
    """Реализованная волатильность в % годовых (по лог-доходностям)."""
    logret = np.log(close / close.shift(1))
    return logret.rolling(period, min_periods=period).std(ddof=0) * np.sqrt(bars_per_year) * 100.0


# ─────────────────────────────────────────────────────────────
#  ТРЕНД / СТРУКТУРА
# ─────────────────────────────────────────────────────────────


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.DataFrame:
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=high.index
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=high.index
    )
    tr = rma(true_range(high, low, close), period)
    tr_safe = tr.replace(0.0, np.nan)
    plus_di = 100.0 * rma(plus_dm, period) / tr_safe
    minus_di = 100.0 * rma(minus_dm, period) / tr_safe
    di_sum = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    adx_val = rma(dx, period).clip(0.0, 100.0)
    return pd.DataFrame(
        {
            "adx": adx_val.fillna(0.0),
            "plus_di": plus_di.fillna(0.0).clip(lower=0.0),
            "minus_di": minus_di.fillna(0.0).clip(lower=0.0),
        },
        index=high.index,
    )


def supertrend(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 10, mult: float = 3.0
) -> pd.DataFrame:
    """SuperTrend. st_dir: +1 бычий / -1 медвежий (без пропусков)."""
    hl2 = (high + low) / 2.0
    rng = atr(high, low, close, period)
    upper = (hl2 + mult * rng).to_numpy(dtype=float)
    lower = (hl2 - mult * rng).to_numpy(dtype=float)
    c = close.to_numpy(dtype=float)
    n = len(c)

    f_upper = upper.copy()
    f_lower = lower.copy()
    trend = np.ones(n, dtype=float)

    for i in range(1, n):
        if not np.isfinite(f_upper[i - 1]) or not np.isfinite(f_lower[i - 1]):
            trend[i] = trend[i - 1]
            continue
        f_upper[i] = upper[i] if (upper[i] < f_upper[i - 1] or c[i - 1] > f_upper[i - 1]) else f_upper[i - 1]
        f_lower[i] = lower[i] if (lower[i] > f_lower[i - 1] or c[i - 1] < f_lower[i - 1]) else f_lower[i - 1]
        prev = trend[i - 1]
        if prev > 0:
            trend[i] = -1.0 if c[i] < f_lower[i] else 1.0
        else:
            trend[i] = 1.0 if c[i] > f_upper[i] else -1.0

    st_dir = pd.Series(np.where(trend > 0, 1, -1), index=close.index)
    st_trend = pd.Series(np.where(trend > 0, f_lower, f_upper), index=close.index)
    st_trend = st_trend.where(np.isfinite(st_trend), np.nan)
    return pd.DataFrame({"st_dir": st_dir, "st_trend": st_trend}, index=close.index)


def vwap(df: pd.DataFrame) -> pd.Series:
    """Сессионный VWAP: сбрасывается на границе суток (UTC)."""
    ts = df["ts"]
    if pd.api.types.is_numeric_dtype(ts):
        day = (ts.astype("int64") // 86_400_000)
    else:
        day = pd.to_datetime(ts).dt.floor("D").astype("int64") // 86_400_000
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = typical * df["volume"]
    cum_pv = pv.groupby(day).cumsum()
    cum_v = df["volume"].groupby(day).cumsum().replace(0.0, np.nan)
    out = cum_pv / cum_v
    return out.ffill().fillna(typical)


# ─────────────────────────────────────────────────────────────
#  ОБЪЁМ / ПОТОК ДЕНЕГ
# ─────────────────────────────────────────────────────────────


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff()).fillna(0.0)
    return (direction * volume).cumsum()


def mfi(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, period: int = 14) -> pd.Series:
    typical = (high + low + close) / 3.0
    raw = typical * volume
    diff = typical.diff()
    pos = pd.Series(np.where(diff > 0, raw, 0.0), index=close.index)
    neg = pd.Series(np.where(diff < 0, raw, 0.0), index=close.index)
    pos_sum = pos.rolling(period, min_periods=period).sum()
    neg_sum = neg.rolling(period, min_periods=period).sum().replace(0.0, np.nan)
    ratio = pos_sum / neg_sum
    out = 100.0 - 100.0 / (1.0 + ratio)
    out = out.where(neg_sum != 0.0, 100.0)
    return out.clip(0.0, 100.0).fillna(50.0)


def cvd(df: pd.DataFrame) -> pd.Series:
    """
    Cumulative Volume Delta — прокси дельты по OHLCV.
    Доля покупного объёма в баре оценивается позицией закрытия в диапазоне.
    """
    rng = (df["high"] - df["low"]).replace(0.0, np.nan)
    buy_ratio = ((df["close"] - df["low"]) / rng).clip(0.0, 1.0).fillna(0.5)
    delta = df["volume"] * (2.0 * buy_ratio - 1.0)
    return delta.cumsum()


def volume_zscore(volume: pd.Series, period: int = 20) -> pd.Series:
    mean = volume.rolling(period, min_periods=period).mean()
    std = volume.rolling(period, min_periods=period).std(ddof=0).replace(0.0, np.nan)
    return ((volume - mean) / std).fillna(0.0)


def trend_slope(series: pd.Series, period: int = 10) -> pd.Series:
    """Нормированный наклон (направление тренда серии): знак = направление."""
    out = series.diff(period)
    scale = series.abs().rolling(period, min_periods=period).mean().replace(0.0, np.nan)
    return (out / scale).fillna(0.0)


# ─────────────────────────────────────────────────────────────
#  СВОДНАЯ ТАБЛИЦА
# ─────────────────────────────────────────────────────────────


def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    """
    Добавляет к OHLCV-фрейму весь набор индикаторов.
    Ожидает колонки: ts, open, high, low, close, volume.
    """
    out = df.copy()
    if out.empty:
        return out
    for col in ("open", "high", "low", "close", "volume"):
        out[col] = pd.to_numeric(out[col], errors="coerce")

    h, low_, c, v = out["high"], out["low"], out["close"], out["volume"]

    out["ema_9"] = ema(c, 9)
    out["ema_20"] = ema(c, 20)
    out["ema_50"] = ema(c, 50)
    out["ema_200"] = ema(c, 200)

    out["rsi_14"] = rsi(c, 14)
    m = macd(c)
    out["macd"] = m["macd"]
    out["macd_signal"] = m["macd_signal"]
    out["macd_hist"] = m["macd_hist"]
    st = stochastic(h, low_, c)
    out["stoch_k"] = st["stoch_k"]
    out["stoch_d"] = st["stoch_d"]
    out["roc_20"] = roc(c, 20)
    out["wpr_14"] = williams_r(h, low_, c, 14)

    out["atr_14"] = atr(h, low_, c, 14)
    out["atr_pct"] = (out["atr_14"] / c * 100.0).clip(0.0, 100.0)
    out["atr_pctl"] = (
        out["atr_14"].rolling(100, min_periods=20).rank(pct=True).fillna(0.5)
    )
    bb = bollinger(c, 20, 2.0)
    out["bb_mid"] = bb["bb_mid"]
    out["bb_up"] = bb["bb_up"]
    out["bb_low"] = bb["bb_low"]
    out["bb_width"] = bb["bb_width"]
    out["bb_pctb"] = bb["bb_pctb"]
    kc = keltner(h, low_, c, 20, 1.5)
    out["kc_up"] = kc["kc_up"]
    out["kc_low"] = kc["kc_low"]
    out["rv_20"] = realized_vol(c, 20)
    out["squeeze"] = (out["bb_up"] < out["kc_up"]) & (out["bb_low"] > out["kc_low"])

    a = adx(h, low_, c)
    out["adx"] = a["adx"]
    out["plus_di"] = a["plus_di"]
    out["minus_di"] = a["minus_di"]

    stt = supertrend(h, low_, c)
    out["st_dir"] = stt["st_dir"]
    out["st_trend"] = stt["st_trend"]

    out["vwap"] = vwap(out)
    out["obv"] = obv(c, v)
    out["cvd"] = cvd(out)
    out["mfi_14"] = mfi(h, low_, c, v, 14)
    out["vol_z"] = volume_zscore(v, 20)
    out["obv_trend"] = trend_slope(out["obv"], 10)
    out["cvd_trend"] = trend_slope(out["cvd"], 10)
    return out


def last(col: pd.Series, default: float = 0.0) -> float:
    """Последнее конечное значение колонки (без NaN)."""
    s = col.dropna()
    if s.empty:
        return default
    v = float(s.iloc[-1])
    return v if np.isfinite(v) else default


def percentile_rank(series: pd.Series, value: float) -> float:
    s = series.dropna()
    if s.empty:
        return 0.5
    return float((s < value).sum()) / float(len(s))
