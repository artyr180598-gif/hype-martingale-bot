import time
import os
import requests
from datetime import datetime, timezone

# ── СТРАТЕГИЯ (config v3.2.0) ─────────────────────────────────────
SYMBOL   = "HYPEUSDT"
LEVERAGE = 20

MARGINS = [
    155, 170, 187, 206, 227,
    250, 275, 302, 332, 365,
    402, 442, 486, 535, 588,
    647, 712
]
ENTRY_DROP_PCT     = 0.6
AVERAGING_STEP_PCT = 1.45
SMART_TP = [
    0.7, 0.8, 1.0, 1.0, 1.0,
    1.0, 1.0, 1.0, 1.0, 1.0,
    1.0, 1.0, 1.0, 1.0, 1.0,
    1.0, 1.0
]
STOP_LOSS_PCT   = 29.0
COMMISSION_PCT  = 0.1
SLIPPAGE_PCT    = 0.05     # реализм: 0.05% на каждое исполнение
FUNDING_8H_PCT  = 0.01
START_BALANCE   = 5500.0

DAYS     = 365
INTERVAL = "15"            # 15m: год = ~35k свечей, надёжно

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def tg_send(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=10
        )
    except Exception:
        pass


def fetch_klines(days: int, interval: str) -> list:
    """Пагинация ВПЕРЁД от стартовой даты — надёжно для длинной истории."""
    interval_ms = int(interval) * 60 * 1000
    end_ms      = int(time.time() * 1000)
    start_ms    = end_ms - days * 24 * 60 * 60 * 1000
    out         = []
    cursor      = start_ms
    empty_tries = 0

    print(f"Скачиваю {days} дней свечей {interval}m...")
    while cursor < end_ms:
        try:
            r = requests.get(
                "https://api.bybit.com/v5/market/kline",
                params={
                    "category": "linear",
                    "symbol":   SYMBOL,
                    "interval": interval,
                    "start":    cursor,
                    "limit":    1000
                },
                timeout=15
            ).json()
        except Exception as e:
            print(f"Ошибка сети: {e}, повтор через 2с")
            time.sleep(2)
            continue

        rows = r.get("result", {}).get("list", [])
        if not rows:
            # либо монета ещё не торговалась, либо конец данных
            empty_tries += 1
            if empty_tries > 3:
                break
            cursor += 1000 * interval_ms   # перепрыгнуть пустой период
            continue
        empty_tries = 0

        rows.sort(key=lambda x: int(x[0]))   # Bybit отдаёт от новых к старым
        for row in rows:
            ts = int(row[0])
            if ts >= end_ms:
                continue
            out.append((
                ts,
                float(row[1]), float(row[2]),
                float(row[3]), float(row[4]),
            ))

        newest = int(rows[-1][0])
        if newest <= cursor:
            break
        cursor = newest + interval_ms
        print(f"  ...{len(out)} свечей", end="\r")
        time.sleep(0.05)

    # дедупликация и сортировка
    seen, clean = set(), []
    for c in sorted(out, key=lambda x: x[0]):
        if c[0] not in seen:
            seen.add(c[0])
            clean.append(c)

    if clean:
        d1 = datetime.fromtimestamp(clean[0][0] / 1000)
        d2 = datetime.fromtimestamp(clean[-1][0] / 1000)
        print(f"\nГотово: {len(clean)} свечей | {d1:%d.%m.%Y} → {d2:%d.%m.%Y}")
    return clean


def run_backtest(candles: list) -> dict:
    balance      = START_BALANCE
    peak_equity  = START_BALANCE
    max_eq_dd    = 0.0
    max_eq_dd_ts = None

    in_trade   = False
    entries    = []
    level      = 0
    first_price = None
    avg_price  = 0.0
    total_qty  = 0.0
    invested   = 0.0
    recent_high = None
    last_funding = None

    trades, stops = [], []
    level_dist, monthly = {}, {}
    insolvent = False

    def mk(ts):
        d = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        return f"{d.year}-{d.month:02d}"

    def open_level(price, ts):
        nonlocal balance, level, avg_price, total_qty, invested
        fill   = price * (1 + SLIPPAGE_PCT / 100)   # слипедж против нас
        margin = MARGINS[level]
        qty    = (margin * LEVERAGE) / fill
        comm   = qty * fill * COMMISSION_PCT / 100
        if margin + comm > balance:
            return False
        balance  -= margin + comm
        entries.append((fill, qty, margin))
        total_qty += qty
        invested  += margin
        avg_price  = sum(p * q for p, q, _ in entries) / sum(q for _, q, _ in entries)
        level     += 1
        return True

    def close_position(price, ts, is_stop):
        nonlocal balance, in_trade, entries, level, first_price
        nonlocal avg_price, total_qty, invested
        fill  = price * (1 - SLIPPAGE_PCT / 100)
        gross = total_qty * (fill - avg_price)
        comm  = total_qty * fill * COMMISSION_PCT / 100
        net   = gross - comm
        balance += invested + net
        monthly[mk(ts)] = monthly.get(mk(ts), 0.0) + net
        level_dist[level] = level_dist.get(level, 0) + 1
        rec = {"ts": ts, "levels": level, "net": net, "is_stop": is_stop}
        trades.append(rec)
        if is_stop:
            stops.append(rec)
        in_trade, entries, level = False, [], 0
        first_price, avg_price, total_qty, invested = None, 0.0, 0.0, 0.0

    for ts, o, high, low, close in candles:
        if insolvent:
            break

        if in_trade:
            h8 = ts // (8 * 3600 * 1000)
            if last_funding != h8:
                last_funding = h8
                balance -= total_qty * avg_price * FUNDING_8H_PCT / 100

        # ── просадка эквити по худшей точке свечи ──
        if in_trade:
            worst_eq = balance + invested + total_qty * (low - avg_price)
        else:
            worst_eq = balance
        peak_equity = max(peak_equity, worst_eq)
        dd = (peak_equity - worst_eq) / peak_equity * 100
        if dd > max_eq_dd:
            max_eq_dd, max_eq_dd_ts = dd, ts

        if not in_trade:
            if recent_high is None or high > recent_high:
                recent_high = high
            trigger = recent_high * (1 - ENTRY_DROP_PCT / 100)
            if low <= trigger:
                if open_level(min(trigger, o), ts):
                    in_trade, first_price = True, min(trigger, o)
                else:
                    insolvent = True
        else:
            sl = first_price * (1 - STOP_LOSS_PCT / 100)
            if low <= sl:
                close_position(sl, ts, True)
                recent_high = close
                continue
            while level < len(MARGINS):
                lp = first_price * (1 - level * AVERAGING_STEP_PCT / 100)
                if low <= lp:
                    if not open_level(lp, ts):
                        insolvent = True
                        break
                else:
                    break
            tp_pct = SMART_TP[min(max(level - 1, 0), len(SMART_TP) - 1)]
            tp = avg_price * (1 + tp_pct / 100)
            if high >= tp:
                close_position(tp, ts, False)
                recent_high = close

    open_pnl = total_qty * (candles[-1][4] - avg_price) if in_trade else 0.0
    return {
        "balance": balance, "open_pnl": open_pnl,
        "invested_now": invested, "in_trade": in_trade,
        "level_now": level, "trades": trades, "stops": stops,
        "level_dist": level_dist, "monthly": monthly,
        "max_eq_dd": max_eq_dd, "max_eq_dd_ts": max_eq_dd_ts,
        "insolvent": insolvent,
        "period": (candles[0][0], candles[-1][0])
    }


def report(res: dict) -> str:
    tr, st = res["trades"], res["stops"]
    n, ns  = len(tr), len(st)
    wr     = round((n - ns) / n * 100, 1) if n else 0
    total  = sum(t["net"] for t in tr)
    end_eq = res["balance"] + res["invested_now"] + res["open_pnl"]
    d1 = datetime.fromtimestamp(res["period"][0] / 1000)
    d2 = datetime.fromtimestamp(res["period"][1] / 1000)
    cov = (res["period"][1] - res["period"][0]) / 86400000

    L = [
        f"📊 БЭКТЕСТ {SYMBOL} | {INTERVAL}m",
        f"Период: {d1:%d.%m.%Y} → {d2:%d.%m.%Y} ({cov:.0f} дн)",
        f"Конфиг: {len(MARGINS)} ур | шаг {AVERAGING_STEP_PCT}% | СЛ -{STOP_LOSS_PCT}% | слипедж {SLIPPAGE_PCT}%",
        "═" * 34,
        f"💰 Старт: ${START_BALANCE:,.0f} → Итог: ${end_eq:,.2f}",
        f"📈 PnL закрытый: ${total:+,.2f}",
        f"✅ {n} сд | ❌ {ns} стопов | 🏆 {wr}%",
        f"📉 МАКС ПРОСАДКА ЭКВИТИ: -{res['max_eq_dd']:.1f}%",
    ]
    if res["max_eq_dd_ts"]:
        L.append(f"   (дата: {datetime.fromtimestamp(res['max_eq_dd_ts']/1000):%d.%m.%Y})")
    if res["in_trade"]:
        L.append(f"⚠️ Открытая поз.: ур.{res['level_now']}, PnL ${res['open_pnl']:+,.2f}")
    if res["insolvent"]:
        L.append("🚨 БАЛАНСА НЕ ХВАТИЛО — стратегия сломалась!")
    L.append("─" * 34)
    L.append("Макс. уровень за сделку:")
    for lvl in sorted(res["level_dist"]):
        L.append(f"  ур.{lvl:>2}: {res['level_dist'][lvl]:>4}")
    L.append("─" * 34)
    L.append("По месяцам:")
    for m in sorted(res["monthly"]):
        L.append(f"  {m}: ${res['monthly'][m]:+,.2f}")
    if st:
        L.append("─" * 34)
        L.append("Стопы:")
        for s in st:
            L.append(f"  {datetime.fromtimestamp(s['ts']/1000):%d.%m.%Y} | ур.{s['levels']} | ${s['net']:,.2f}")
    return "\n".join(L)


if __name__ == "__main__":
    candles = fetch_klines(DAYS, INTERVAL)
    if not candles:
        tg_send("🚨 Бэктест: не удалось скачать свечи")
    else:
        res  = run_backtest(candles)
        text = report(res)
        print(text)
        for i in range(0, len(text), 3900):
            tg_send(text[i:i + 3900])
            time.sleep(1)
