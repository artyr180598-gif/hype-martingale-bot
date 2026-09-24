import concurrent.futures, math, time
from dataclasses import dataclass, field
from typing import Any
import pandas as pd, requests

# Signal-only market intelligence engine.
# Architecture inspired by live market-intelligence terminals:
# multi-venue price/flow context -> structure -> liquidity -> OI/funding -> scoring.
# It NEVER creates exchange orders.

TIMEOUT=7
MAX_SYMBOLS=60
MIN_TURNOVER=2_000_000
MAX_WORKERS=10
BASES={
    "bybit":"https://api.bybit.com",
    "binance":"https://fapi.binance.com",
    "okx":"https://www.okx.com",
}
session=requests.Session()
session.headers.update({"User-Agent":"Hype-Mega-Radar/5.0"})

@dataclass
class Snapshot:
    symbol:str
    price:float
    volume24:float
    change24:float
    oi_pct:float=0.0
    funding:float=0.0
    obi:float=0.0
    flow:float=0.0
    venues:int=1
    health:list[str]=field(default_factory=list)

@dataclass
class Signal:
    symbol:str
    side:str
    score:int
    price:float
    entry_low:float
    entry_high:float
    stop:float
    tp1:float
    tp2:float
    tp3:float
    rr:float
    reasons:list[str]
    warnings:list[str]
    data_quality:str="LIVE"

def get_json(base,path,params):
    r=session.get(base+path,params=params,timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()

def bybit(path,params):
    x=get_json(BASES["bybit"],path,params)
    if x.get("retCode")!=0: raise RuntimeError(x.get("retMsg","Bybit error"))
    return x["result"]

def binance(path,params):
    return get_json(BASES["binance"],path,params)

def okx(path,params):
    x=get_json(BASES["okx"],path,params)
    if x.get("code")!="0": raise RuntimeError(x.get("msg","OKX error"))
    return x["data"]

def universe():
    rows=bybit("/v5/market/tickers",{"category":"linear"})["list"]
    out=[]
    for x in rows:
        s=x.get("symbol","")
        q=float(x.get("turnover24h") or 0)
        if not s.endswith("USDT") or q<MIN_TURNOVER: continue
        if any(k in s for k in ("UPUSDT","DOWNUSDT","BULLUSDT","BEARUSDT")): continue
        out.append(x)
    return sorted(out,key=lambda x:float(x.get("turnover24h") or 0),reverse=True)[:MAX_SYMBOLS]

def candles(symbol,interval,limit=240):
    rows=bybit("/v5/market/kline",{"category":"linear","symbol":symbol,"interval":interval,"limit":limit})["list"][::-1]
    d=pd.DataFrame(rows,columns=["ts","open","high","low","close","volume","turnover"])
    for c in d.columns[1:]: d[c]=pd.to_numeric(d[c],errors="coerce")
    return d.dropna().reset_index(drop=True)

def meta(symbol):
    oi=0.0; funding=0.0; obi=0.0; flow=0.0; health=[]
    try:
        rows=bybit("/v5/market/open-interest",{"category":"linear","symbol":symbol,"intervalTime":"5min","limit":3})["list"]
        if len(rows)>=2:
            now=float(rows[0]["openInterest"]); old=float(rows[-1]["openInterest"])
            oi=(now/old-1)*100 if old else 0.0
    except Exception as e: health.append("OI unavailable")
    try:
        t=bybit("/v5/market/tickers",{"category":"linear","symbol":symbol})["list"][0]
        funding=float(t.get("fundingRate") or 0)*100
    except Exception: health.append("funding unavailable")
    try:
        x=bybit("/v5/market/orderbook",{"category":"linear","symbol":symbol,"limit":50})
        b=sum(float(p)*float(q) for p,q in x["b"]); a=sum(float(p)*float(q) for p,q in x["a"])
        obi=(b-a)/max(b+a,1e-9)
    except Exception: health.append("orderbook unavailable")
    try:
        rows=bybit("/v5/market/recent-trade",{"category":"linear","symbol":symbol,"limit":1000})["list"]
        buy=sum(float(x["size"]) for x in rows if x.get("side")=="Buy")
        sell=sum(float(x["size"]) for x in rows if x.get("side")=="Sell")
        flow=(buy-sell)/max(buy+sell,1e-9)
    except Exception: health.append("trade flow unavailable")
    return oi,funding,obi,flow,health

def cross_venue(symbol,price):
    # A second venue is confirmation only; missing venue data is never fabricated.
    confirmations=1; health=[]
    base=symbol.replace("USDT","USDT")
    try:
        x=binance("/fapi/v1/ticker/price",{"symbol":base})
        p=float(x["price"])
        if abs(p-price)/price<0.003: confirmations+=1
    except Exception: health.append("Binance unavailable")
    try:
        # OKX uses BTC-USDT-SWAP style instruments.
        inst=base[:-4]+"-USDT-SWAP"
        x=okx("/api/v5/market/ticker",{"instId":inst})
        if x:
            p=float(x[0]["last"])
            if abs(p-price)/price<0.003: confirmations+=1
    except Exception: health.append("OKX unavailable")
    return confirmations,health

def features(d):
    x=d.copy(); c,h,l,o,v=x.close,x.high,x.low,x.open,x.volume
    x["e20"]=c.ewm(span=20,adjust=False).mean()
    x["e50"]=c.ewm(span=50,adjust=False).mean()
    x["e200"]=c.ewm(span=200,adjust=False).mean()
    delta=c.diff(); gain=delta.clip(lower=0).ewm(alpha=1/14,adjust=False).mean()
    loss=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean()
    x["rsi"]=100-100/(1+gain/loss.replace(0,math.nan))
    tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    x["atr"]=tr.ewm(alpha=1/14,adjust=False).mean()
    x["vr"]=v/v.rolling(30).mean().replace(0,math.nan)
    x["vwap"]=((h+l+c)/3*v).rolling(48).sum()/v.rolling(48).sum().replace(0,math.nan)
    ph=(h.shift(2)>h.shift(3))&(h.shift(2)>h.shift(1))&(h.shift(2)>h.shift(4))&(h.shift(2)>h)
    pl=(l.shift(2)<l.shift(3))&(l.shift(2)<l.shift(4))&(l.shift(2)<l.shift(1))&(l.shift(2)<l)
    x["sh"]=h.shift(2).where(ph).ffill(); x["sl"]=l.shift(2).where(pl).ffill()
    x["bos_up"]=c>x.sh.shift(1); x["bos_dn"]=c<x.sl.shift(1)
    x["sweep_low"]=(l<x.sl.shift(1))&(c>x.sl.shift(1))
    x["sweep_high"]=(h>x.sh.shift(1))&(c<x.sh.shift(1))
    x["fvg_up"]=l>h.shift(2); x["fvg_dn"]=h<l.shift(2)
    x["body"]=(c-o)/(h-l).replace(0,math.nan); x["flow"]=x.body*x.vr
    x["ext"]=(c-x.e20).abs()/x.atr.replace(0,math.nan)
    return x

def regime(a,b):
    long_a=a.e20>a.e50>a.e200; long_b=b.e20>b.e50>b.e200
    short_a=a.e20<a.e50<a.e200; short_b=b.e20<b.e50<b.e200
    if long_a and long_b:return "LONG"
    if short_a and short_b:return "SHORT"
    if long_a or long_b:return "LONG_BIAS"
    if short_a or short_b:return "SHORT_BIAS"
    return "MIXED"

def build(sym,d5,d15,d60,oi,fr,obi,tflow,venues,health):
    x=d5.iloc[-1]; prev=float(d5.close.iloc[-2]); a=d15.iloc[-1]; b=d60.iloc[-1]
    reg=regime(a,b); out=[]
    if reg=="MIXED": return out
    for side in ("LONG","SHORT"):
        bull=side=="LONG"; bias="LONG_BIAS" if bull else "SHORT_BIAS"
        score=24 if reg==side else (15 if reg==bias else 0)
        reasons=["1h + 15m regime aligned" if reg==side else "higher-timeframe bias"]
        warnings=list(health)
        if venues<2: warnings.append("cross-venue confirmation partial")
        if bull:
            if x.bos_up: score+=15; reasons.append("BOS up")
            if x.sweep_low: score+=17; reasons.append("liquidity sweep")
            if x.fvg_up: score+=7; reasons.append("bullish FVG")
            if x.flow>0.10: score+=6; reasons.append("positive candle flow")
            if tflow>0.08: score+=9; reasons.append("recent trades buyer-dominant")
            if 47<=x.rsi<=70: score+=5; reasons.append("RSI supports continuation")
            if x.close>x.vwap: score+=4; reasons.append("above VWAP")
            if obi>0.05: score+=5; reasons.append("bid-side depth")
            if oi>0.6 and x.close>prev: score+=7; reasons.append(f"OI +{oi:.1f}% with price")
            if fr>0.08: warnings.append(f"funding elevated +{fr:.3f}%")
            trigger=bool(x.bos_up or x.sweep_low or x.fvg_up or tflow>0.18)
            if x.ext>2.0: score-=10; warnings.append("price extended from EMA")
            entry=float(x.close); anchor=float(x.sl) if pd.notna(x.sl) else entry-float(x.atr)
            stop=min(anchor-.25*float(x.atr),entry-.8*float(x.atr)); risk=entry-stop
            if risk<=0: continue
            lo=min(entry,entry-.35*float(x.atr) if x.fvg_up else entry); hi=max(entry,entry+.15*float(x.atr) if x.bos_up else entry)
            tp1=hi+1.2*risk; tp2=hi+2*risk; tp3=hi+3*risk
        else:
            if x.bos_dn: score+=15; reasons.append("BOS down")
            if x.sweep_high: score+=17; reasons.append("liquidity sweep")
            if x.fvg_dn: score+=7; reasons.append("bearish FVG")
            if x.flow<-.10: score+=6; reasons.append("negative candle flow")
            if tflow<-.08: score+=9; reasons.append("recent trades seller-dominant")
            if 30<=x.rsi<=53: score+=5; reasons.append("RSI supports continuation")
            if x.close<x.vwap: score+=4; reasons.append("below VWAP")
            if obi<-.05: score+=5; reasons.append("ask-side depth")
            if oi>0.6 and x.close<prev: score+=7; reasons.append(f"OI +{oi:.1f}% with price")
            if fr<-.08: warnings.append(f"funding negative {fr:.3f}%")
            trigger=bool(x.bos_dn or x.sweep_high or x.fvg_dn or tflow<-.18)
            if x.ext>2.0: score-=10; warnings.append("price extended from EMA")
            entry=float(x.close); anchor=float(x.sh) if pd.notna(x.sh) else entry+float(x.atr)
            stop=max(anchor+.25*float(x.atr),entry+.8*float(x.atr)); risk=stop-entry
            if risk<=0: continue
            lo=min(entry,entry-.15*float(x.atr) if x.bos_dn else entry); hi=max(entry,entry+.35*float(x.atr) if x.fvg_dn else entry)
            tp1=lo-1.2*risk; tp2=lo-2*risk; tp3=lo-3*risk
        if not trigger or not math.isfinite(score) or score<55 or float(x.vr)<.60 or float(x.ext)>3.0: continue
        rr=2.0
        if venues>=2: score+=3; reasons.append("multi-venue price confirmation")
        score=int(max(0,min(100,score)))
        out.append(Signal(sym,side,score,float(x.close),lo,hi,stop,tp1,tp2,tp3,rr,reasons,warnings))
    return out

def analyze(t):
    sym=t["symbol"]
    d5=features(candles(sym,"5")); d15=features(candles(sym,"15")); d60=features(candles(sym,"60"))
    oi,fr,obi,tflow,health=meta(sym)
    price=float(d5.close.iloc[-1])
    venues,health2=cross_venue(sym,price); health.extend(health2)
    return build(sym,d5,d15,d60,oi,fr,obi,tflow,venues,health)

def scan():
    started=time.time()
    assets=universe()
    results=[]; failures=0
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        fs={ex.submit(analyze,t):t["symbol"] for t in assets}
        for f in concurrent.futures.as_completed(fs):
            try: results.extend(f.result())
            except Exception: failures+=1
    results.sort(key=lambda s:s.score,reverse=True)
    # Attach transparent runtime stats to the list without changing Telegram's signal API.
    scan.last_stats={"universe":len(assets),"failed":failures,"signals":len(results),"seconds":round(time.time()-started,1)}
    return results

scan.last_stats={"universe":0,"failed":0,"signals":0,"seconds":0.0}
