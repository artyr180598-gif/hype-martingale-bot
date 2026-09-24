import concurrent.futures, math, time
from dataclasses import dataclass
import pandas as pd, requests

BASE="https://api.bybit.com"; TIMEOUT=8; MAX_SYMBOLS=80
session=requests.Session(); session.headers["User-Agent"]="Hype-Signal-Radar/4.0"

@dataclass
class Signal:
    symbol:str; side:str; score:int; price:float; entry_low:float; entry_high:float
    stop:float; tp1:float; tp2:float; tp3:float; rr:float; reasons:list[str]; warnings:list[str]

def api(path, params):
    r=session.get(BASE+path,params=params,timeout=TIMEOUT); r.raise_for_status()
    x=r.json()
    if x.get("retCode")!=0: raise RuntimeError(x.get("retMsg","Bybit error"))
    return x["result"]

def tickers():
    rows=api("/v5/market/tickers",{"category":"linear"})["list"]; out=[]
    for x in rows:
        s=x.get("symbol",""); q=float(x.get("turnover24h",0) or 0)
        if s.endswith("USDT") and q>=1_000_000 and not any(k in s for k in ("UPUSDT","DOWNUSDT","BULLUSDT","BEARUSDT")):
            out.append(x)
    return sorted(out,key=lambda x:float(x.get("turnover24h",0) or 0),reverse=True)[:MAX_SYMBOLS]

def klines(symbol,interval,limit=240):
    rows=api("/v5/market/kline",{"category":"linear","symbol":symbol,"interval":interval,"limit":limit})["list"][::-1]
    d=pd.DataFrame(rows,columns=["ts","open","high","low","close","volume","turnover"])
    for c in d.columns[1:]: d[c]=pd.to_numeric(d[c],errors="coerce")
    return d.dropna().reset_index(drop=True)

def market_meta(symbol):
    oi_pct=0.0; funding=0.0; obi=0.0; trade_flow=0.0
    try:
        rows=api("/v5/market/open-interest",{"category":"linear","symbol":symbol,"intervalTime":"5min","limit":2})["list"]
        now,old=float(rows[0]["openInterest"]),float(rows[-1]["openInterest"])
        oi_pct=(now/old-1)*100 if old else 0
    except Exception: pass
    try:
        funding=float(api("/v5/market/tickers",{"category":"linear","symbol":symbol})["list"][0].get("fundingRate",0) or 0)*100
    except Exception: pass
    try:
        x=api("/v5/market/orderbook",{"category":"linear","symbol":symbol,"limit":20})
        b=sum(float(p)*float(q) for p,q in x["b"]); a=sum(float(p)*float(q) for p,q in x["a"])
        obi=(b-a)/max(b+a,1e-9)
    except Exception: pass
    # Recent public trades: a real short-window buy/sell-flow snapshot, not a fake historical CVD.
    try:
        rows=api("/v5/market/recent-trade",{"category":"linear","symbol":symbol,"limit":1000})["list"]
        buy=sum(float(x["size"]) for x in rows if x.get("side")=="Buy")
        sell=sum(float(x["size"]) for x in rows if x.get("side")=="Sell")
        trade_flow=(buy-sell)/max(buy+sell,1e-9)
    except Exception: pass
    return oi_pct,funding,obi,trade_flow

def feat(d):
    x=d.copy(); c,h,l,o,v=x.close,x.high,x.low,x.open,x.volume
    x["e20"]=c.ewm(span=20,adjust=False).mean(); x["e50"]=c.ewm(span=50,adjust=False).mean(); x["e200"]=c.ewm(span=200,adjust=False).mean()
    delta=c.diff(); gain=delta.clip(lower=0).ewm(alpha=1/14,adjust=False).mean(); loss=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean()
    x["rsi"]=100-100/(1+gain/loss.replace(0,math.nan))
    tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1); x["atr"]=tr.ewm(alpha=1/14,adjust=False).mean()
    x["vr"]=v/v.rolling(30).mean().replace(0,math.nan); x["vwap"]=((h+l+c)/3*v).rolling(48).sum()/v.rolling(48).sum().replace(0,math.nan)
    ph=(h.shift(2)>h.shift(3))&(h.shift(2)>h.shift(1))&(h.shift(2)>h.shift(4))&(h.shift(2)>h); pl=(l.shift(2)<l.shift(3))&(l.shift(2)<l.shift(4))&(l.shift(2)<l.shift(1))&(l.shift(2)<l)
    x["sh"]=h.shift(2).where(ph).ffill(); x["sl"]=l.shift(2).where(pl).ffill()
    x["bos_up"]=c>x.sh.shift(1); x["bos_dn"]=c<x.sl.shift(1); x["sweep_low"]=(l<x.sl.shift(1))&(c>x.sl.shift(1)); x["sweep_high"]=(h>x.sh.shift(1))&(c<x.sh.shift(1))
    x["fvg_up"]=l>h.shift(2); x["fvg_dn"]=h<l.shift(2); x["body"]=(c-o)/(h-l).replace(0,math.nan); x["flow"]=x.body*x.vr; x["ext"]=(c-x.e20).abs()/x.atr.replace(0,math.nan)
    return x

def regime(a,b):
    al=a.e20>a.e50>a.e200; bl=b.e20>b.e50>b.e200; ash=a.e20<a.e50<a.e200; bsh=b.e20<b.e50<b.e200
    if al and bl:return "LONG"
    if ash and bsh:return "SHORT"
    if al or bl:return "LONG_BIAS"
    if ash or bsh:return "SHORT_BIAS"
    return "MIXED"

def build(sym,d5,d15,d60,oi,fr,obi,tflow):
    x=d5.iloc[-1]; prev=float(d5.close.iloc[-2]); a=d15.iloc[-1]; b=d60.iloc[-1]; reg=regime(a,b); out=[]
    for side in ("LONG","SHORT"):
        bull=side=="LONG"; bias="LONG_BIAS" if bull else "SHORT_BIAS"
        if reg=="MIXED": continue
        s=22 if reg==side else (14 if reg==bias else 6); reasons=["1h/15m regime aligned" if reg==side else "higher-timeframe bias"]; warnings=[]
        if bull:
            if x.bos_up:s+=16;reasons.append("BOS up")
            if x.sweep_low:s+=18;reasons.append("liquidity sweep")
            if x.fvg_up:s+=8;reasons.append("bullish FVG")
            if x.flow>0.10:s+=6;reasons.append("candle volume flow")
            if tflow>0.08:s+=8;reasons.append("recent trade flow buy-dominant")
            if 47<=x.rsi<=70:s+=6;reasons.append("RSI regime")
            if x.close>x.vwap:s+=4;reasons.append("above VWAP")
            if obi>0.06:s+=6;reasons.append("orderbook demand")
            if oi>0.8 and x.close>prev:s+=8;reasons.append(f"OI +{oi:.1f}% with price")
            if fr>0.08:warnings.append(f"funding elevated +{fr:.3f}%")
            trigger=bool(x.bos_up or x.sweep_low or x.fvg_up or tflow>0.18)
            if x.ext>2.2:s-=12;warnings.append("extended")
            entry=float(x.close); anchor=float(x.sl) if pd.notna(x.sl) else entry-float(x.atr); stop=min(anchor-.25*float(x.atr),entry-.8*float(x.atr)); risk=entry-stop
            if risk<=0:continue
            lo=min(entry,entry-.35*float(x.atr) if x.fvg_up else entry); hi=max(entry,entry+.15*float(x.atr) if x.bos_up else entry)
            tp1=hi+1.3*risk;tp2=hi+2*risk;tp3=hi+3*risk
        else:
            if x.bos_dn:s+=16;reasons.append("BOS down")
            if x.sweep_high:s+=18;reasons.append("liquidity sweep")
            if x.fvg_dn:s+=8;reasons.append("bearish FVG")
            if x.flow<-.10:s+=6;reasons.append("candle volume flow")
            if tflow<-0.08:s+=8;reasons.append("recent trade flow sell-dominant")
            if 30<=x.rsi<=53:s+=6;reasons.append("RSI regime")
            if x.close<x.vwap:s+=4;reasons.append("below VWAP")
            if obi<-.06:s+=6;reasons.append("orderbook supply")
            if oi>0.8 and x.close<prev:s+=8;reasons.append(f"OI +{oi:.1f}% with price")
            if fr<-.08:warnings.append(f"funding negative {fr:.3f}%")
            trigger=bool(x.bos_dn or x.sweep_high or x.fvg_dn or tflow<-.18)
            if x.ext>2.2:s-=12;warnings.append("extended")
            entry=float(x.close); anchor=float(x.sh) if pd.notna(x.sh) else entry+float(x.atr); stop=max(anchor+.25*float(x.atr),entry+.8*float(x.atr)); risk=stop-entry
            if risk<=0:continue
            lo=min(entry,entry-.15*float(x.atr) if x.bos_dn else entry); hi=max(entry,entry+.35*float(x.atr) if x.fvg_dn else entry)
            tp1=lo-1.3*risk;tp2=lo-2*risk;tp3=lo-3*risk
        if not trigger or not math.isfinite(s) or s<58 or float(x.vr)<.65 or float(x.ext)>3.0:continue
        rr=abs((tp2-hi if bull else lo-tp2)/max(risk,1e-9))
        if rr<1.5:continue
        out.append(Signal(sym,side,int(min(100,s)),float(x.close),lo,hi,stop,tp1,tp2,tp3,rr,reasons,warnings))
    return out

def one(t):
    sym=t["symbol"]
    d5,d15,d60=feat(klines(sym,"5")),feat(klines(sym,"15")),feat(klines(sym,"60"))
    return build(sym,d5,d15,d60,*market_meta(sym))

def scan():
    assets=tickers(); results=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        futures=[ex.submit(one,t) for t in assets]
        for f in concurrent.futures.as_completed(futures):
            try: results.extend(f.result())
            except Exception: pass
    return sorted(results,key=lambda x:x.score,reverse=True)
