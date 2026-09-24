import math
import time
from dataclasses import dataclass
from typing import Any

import pandas as pd
import requests

BASE = "https://api.bybit.com"
TIMEFRAMES = {"5": 240, "15": 180, "60": 180}
MIN_SCORE = 78
MAX_SYMBOLS = 50
TIMEOUT = 10

session = requests.Session()
session.headers.update({"User-Agent": "Hype-Signal-Radar/2.0"})

@dataclass
class Signal:
    symbol: str
    side: str
    score: int
    price: float
    entry_low: float
    entry_high: float
    stop: float
    tp1: float
    tp2: float
    tp3: float
    rr: float
    reasons: list[str]
    warnings: list[str]

def api(path: str, params: dict) -> dict:
    r = session.get(BASE + path, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if data.get("retCode") != 0:
        raise RuntimeError(data.get("retMsg", "Bybit API error"))
    return data["result"]

def tickers() -> list[dict]:
    rows = api("/v5/market/tickers", {"category": "linear"})["list"]
    out = []
    for x in rows:
        if not x["symbol"].endswith("USDT"):
            continue
        qv = float(x.get("turnover24h", 0) or 0)
        if qv < 2_000_000:
            continue
        if any(x["symbol"].endswith(s) for s in ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")):
            continue
        out.append(x)
    return sorted(out, key=lambda x: float(x.get("turnover24h", 0) or 0), reverse=True)[:MAX_SYMBOLS]

def klines(symbol: str, interval: str, limit: int) -> pd.DataFrame:
    rows = api("/v5/market/kline", {
        "category": "linear", "symbol": symbol, "interval": interval, "limit": limit
    })["list"]
    rows = list(reversed(rows))
    df = pd.DataFrame(rows, columns=["ts","open","high","low","close","volume","turnover"])
    for c in ["open","high","low","close","volume","turnover"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["ts"] = pd.to_datetime(pd.to_numeric(df["ts"]), unit="ms", utc=True)
    return df.dropna().reset_index(drop=True)

def orderbook(symbol: str) -> float:
    try:
        x = api("/v5/market/orderbook", {"category": "linear", "symbol": symbol, "limit": 20})
        bids = [(float(p), float(q)) for p, q in x["b"]]
        asks = [(float(p), float(q)) for p, q in x["a"]]
        b = sum(p*q for p,q in bids)
        a = sum(p*q for p,q in asks)
        return (b-a) / max(b+a, 1e-9)
    except Exception:
        return 0.0

def add_features(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    c, h, l, o, v = d.close, d.high, d.low, d.open, d.volume
    d["ema20"] = c.ewm(span=20, adjust=False).mean()
    d["ema50"] = c.ewm(span=50, adjust=False).mean()
    d["ema200"] = c.ewm(span=200, adjust=False).mean()
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    d["rsi"] = 100 - 100/(1 + gain/loss.replace(0, math.nan))
    tr = pd.concat([(h-l), (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    d["atr"] = tr.ewm(span=14, adjust=False).mean()
    d["vol_ratio"] = v / v.rolling(30).mean().replace(0, math.nan)
    typical = (h+l+c)/3
    d["vwap"] = (typical*v).rolling(48).sum()/v.rolling(48).sum().replace(0, math.nan)

    # Confirmed pivots: a swing is used only after two candles on its right exist.
    ph = (h.shift(2)>h.shift(3))&(h.shift(2)>h.shift(1))&(h.shift(2)>h.shift(4))&(h.shift(2)>h)
    pl = (l.shift(2)<l.shift(3))&(l.shift(2)<l.shift(1))&(l.shift(2)<l.shift(4))&(l.shift(2)<l)
    d["swing_high"] = h.shift(2).where(ph).ffill()
    d["swing_low"] = l.shift(2).where(pl).ffill()

    d["bos_up"] = c > d["swing_high"].shift(1)
    d["bos_dn"] = c < d["swing_low"].shift(1)

    # Liquidity sweep: wick takes the confirmed swing and closes back inside.
    d["sweep_low"] = (l < d["swing_low"].shift(1)) & (c > d["swing_low"].shift(1))
    d["sweep_high"] = (h > d["swing_high"].shift(1)) & (c < d["swing_high"].shift(1))

    # Three-candle imbalance.
    d["fvg_up"] = l > h.shift(2)
    d["fvg_dn"] = h < l.shift(2)

    # Impulse predecessor proxy for an order block.
    d["ob_up"] = (c.shift(1) < o.shift(1)) & d["bos_up"]
    d["ob_dn"] = (c.shift(1) > o.shift(1)) & d["bos_dn"]

    d["range"] = (h-l).replace(0, math.nan)
    d["body"] = (c-o)/d["range"]
    d["flow"] = d["body"]*d["vol_ratio"]
    d["atr_ext"] = (c-d["ema20"]).abs()/d["atr"].replace(0, math.nan)
    return d

def regime(df15: pd.DataFrame, df60: pd.DataFrame) -> tuple[str, list[str]]:
    a, b = df15.iloc[-1], df60.iloc[-1]
    long = a.ema20>a.ema50>a.ema200 and b.ema20>b.ema50>b.ema200
    short = a.ema20<a.ema50<a.ema200 and b.ema20<b.ema50<b.ema200
    if long: return "LONG", ["15m и 1h направлены вверх"]
    if short: return "SHORT", ["15m и 1h направлены вниз"]
    return "MIXED", ["старшие таймфреймы не согласованы"]

def build_signal(symbol: str, d5: pd.DataFrame, d15: pd.DataFrame, d60: pd.DataFrame, ob: float) -> Signal | None:
    x = d5.iloc[-1]
    r15, r60 = d15.iloc[-1], d60.iloc[-1]
    side, rr = regime(d15, d60)
    if side == "MIXED":
        return None

    score = 0
    reasons = []
    warnings = []

    # 20 points: higher-timeframe regime.
    score += 20
    reasons.append("HTF 15m+1h согласованы")

    if side == "LONG":
        structure = bool(x.bos_up or x.sweep_low or x.fvg_up or x.ob_up)
        if not structure: return None
        if x.bos_up: score += 14; reasons.append("BOS вверх")
        if x.sweep_low: score += 16; reasons.append("ликвидность снизу снята и возвращена")
        if x.fvg_up: score += 8; reasons.append("бычий FVG")
        if x.ob_up: score += 8; reasons.append("бычий order-block proxy")
        if 48 <= x.rsi <= 68: score += 8; reasons.append("RSI в рабочей зоне")
        if x.vol_ratio >= 1.25: score += 8; reasons.append(f"объём {x.vol_ratio:.1f}x выше среднего")
        if x.flow > 0.12: score += 6; reasons.append("положительный candle-flow")
        if x.close > x.vwap: score += 5; reasons.append("цена выше VWAP")
        if ob > 0.08: score += 7; reasons.append("стакан подтверждает спрос")
        elif ob < -0.10: score -= 8; warnings.append("стакан против LONG")
        if x.atr_ext > 2.0: score -= 12; warnings.append("цена уже растянута от EMA")
        entry_mid = float(x.close)
        candidates = [entry_mid]
        if x.ob_up: candidates.append(float((x.open+x.close)/2))
        if x.fvg_up: candidates.append(float((x.low+x.high.shift(0) if False else x.low)))
        entry_low, entry_high = min(candidates), max(candidates)
        swing = float(x.sweep_low and x.low or x.swing_low)
        stop = min(swing - 0.25*float(x.atr), entry_low - 0.8*float(x.atr))
        risk = entry_high-stop
        if risk <= 0: return None
        tp1, tp2, tp3 = entry_high+risk*1.5, entry_high+risk*2.2, entry_high+risk*3.0
    else:
        structure = bool(x.bos_dn or x.sweep_high or x.fvg_dn or x.ob_dn)
        if not structure: return None
        if x.bos_dn: score += 14; reasons.append("BOS вниз")
        if x.sweep_high: score += 16; reasons.append("ликвидность сверху снята и возвращена")
        if x.fvg_dn: score += 8; reasons.append("медвежий FVG")
        if x.ob_dn: score += 8; reasons.append("медвежий order-block proxy")
        if 32 <= x.rsi <= 52: score += 8; reasons.append("RSI в рабочей зоне")
        if x.vol_ratio >= 1.25: score += 8; reasons.append(f"объём {x.vol_ratio:.1f}x выше среднего")
        if x.flow < -0.12: score += 6; reasons.append("отрицательный candle-flow")
        if x.close < x.vwap: score += 5; reasons.append("цена ниже VWAP")
        if ob < -0.08: score += 7; reasons.append("стакан подтверждает предложение")
        elif ob > 0.10: score -= 8; warnings.append("стакан против SHORT")
        if x.atr_ext > 2.0: score -= 12; warnings.append("цена уже растянута от EMA")
        entry_mid = float(x.close)
        candidates = [entry_mid]
        if x.ob_dn: candidates.append(float((x.open+x.close)/2))
        entry_low, entry_high = min(candidates), max(candidates)
        swing = float(x.sweep_high and x.high or x.swing_high)
        stop = max(swing + 0.25*float(x.atr), entry_high + 0.8*float(x.atr))
        risk = stop-entry_low
        if risk <= 0: return None
        tp1, tp2, tp3 = entry_low-risk*1.5, entry_low-risk*2.2, entry_low-risk*3.0

    # Hard quality gates: no weak "indicator-only" alerts.
    if score < MIN_SCORE or not math.isfinite(score):
        return None
    if x.vol_ratio < 0.85:
        return None
    if x.atr_ext > 2.4:
        return None
    rr = abs((tp2-entry_high if side=="LONG" else entry_low-tp2) / max(risk, 1e-9))
    if rr < 1.8:
        return None

    return Signal(symbol, side, int(min(score,100)), float(x.close),
                  float(entry_low), float(entry_high), float(stop),
                  float(tp1), float(tp2), float(tp3), float(rr), reasons, warnings)

def scan() -> list[Signal]:
    assets = tickers()
    results = []
    for i, t in enumerate(assets):
        symbol = t["symbol"]
        try:
            d5 = add_features(klines(symbol, "5", TIMEFRAMES["5"]))
            d15 = add_features(klines(symbol, "15", TIMEFRAMES["15"]))
            d60 = add_features(klines(symbol, "60", TIMEFRAMES["60"]))
            ob = orderbook(symbol)
            s = build_signal(symbol, d5, d15, d60, ob)
            if s: results.append(s)
        except Exception:
            continue
        if i and i % 10 == 0:
            time.sleep(0.15)
    return sorted(results, key=lambda s: s.score, reverse=True)
