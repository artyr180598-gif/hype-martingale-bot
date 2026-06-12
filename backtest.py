import time
import os
import requests
from datetime import datetime, timezone

# ── НАСТРОЙКИ СТРАТЕГИИ (точно как в config v3.2.0) ──────────────
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
STOP_LOSS_PCT  = 29.0
COMMISSION_PCT = 0.1
FUNDING_8H_PCT = 0.01      # средний фандинг 0.01% каждые 8ч
START_BALANCE  = 5500.0

# ── ПЕРИОД БЭКТЕСТА ───────────────────────────────────────────────
DAYS     = 365             # сколько дней истории
INTERVAL = "5"             # 5-минутные свечи (быстро и точно)

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
    """Скачать свечи с Bybit. Возвращает [(ts, open, high, low, close), ...] по возрастанию времени."""
    end_ms   = int(time.time() * 1000)
    start_ms = end_ms - days * 24 * 60 * 60 * 1000
    out      = []
    cursor   = end_ms

    print(f"Скачиваю {days} дней свечей {interval}m...")
    while cursor > start_ms:
        r = requests.get(
            "https://api.bybit.com/v5/market/kline",
            params={
                "category": "linear",
                "symbol":   SYMBOL,
                "interval": interval,
                "end":      cursor,
                "limit":    1000
            },
            timeout=15
        ).json()
        rows = r.get("result", {}).get("list", [])
        if not rows:
            break
        for row in rows:
            ts = int(row[0])
            if ts < start_ms:
                continue
            out.append((
                ts,
                float(row[1]),  # open
                float(row[2]),  # high
                float(row[3]),  # low
                float(row[4]),  # close
            ))
        oldest = int(rows[-1][0])
        if oldest >= cursor:
            break
        cursor = oldest - 1
        print(f"  ...загружено {len(out)} свечей", end="\r")
        time.sleep(0.05)

    out.sort(key=lambda x: x[0])
    print(f"\nГотово: {len(out)} свечей "
          f"({datetime.fromtimestamp(out[0][0]/1000):%d.%m.%Y} → "
          f"{datetime.fromtimestamp(out[-1][0]/1000):%d.%m.%Y})")
    return out


def run_backtest(candles: list) -> dict:
    balance        = START_BALANCE
    peak_balance   = START_BALANCE
    max_drawdown   = 0.0

    in_trade       = False
    entries        = []        # (price, qty, margin)
    level          = 0
    first_price    = None
    avg_price      = 0.0
    total_qty      = 0.0
    invested       = 0.0
    recent_high    = None
    last_funding   = None

    trades         = []        # закрытые сделки
    stops          = []
    level_dist     = {}        # макс. уровень → сколько раз
    monthly        = {}        # 'YYYY-MM' → pnl

    insolvent      = False

    def month_key(ts):
        d = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        return f"{d.year}-{d.month:02d}"

    def open_level(price, ts):
        nonlocal balance, level, avg_price, total_qty, invested
        margin = MARGINS[level]
        qty    = (margin * LEVERAGE) / price
        comm   = qty * price * COMMISSION_PCT / 100
        cost   = margin + comm
        if cost > balance:
            return False
        balance  -= cost
        entries.append((price, qty, margin))
        total_qty += qty
        invested  += margin
        cost_sum   = sum(p * q for p, q, _ in entries)
        qty_sum    = sum(q for _, q, _ in entries)
        avg_price  = cost_sum / qty_sum
        level     += 1
        return True

    def close_position(price, ts, is_stop):
        nonlocal balance, in_trade, entries, level, first_price
        nonlocal avg_price, total_qty, invested, peak_balance, max_drawdown
        gross      = total_qty * (price - avg_price)
        close_comm = total_qty * price * COMMISSION_PCT / 100
        net        = gross - close_comm
        balance   += invested + net

        mk = month_key(ts)
        monthly[mk] = monthly.get(mk, 0.0) + net
        level_dist[level] = level_dist.get(level, 0) + 1

        rec = {
            "ts": ts, "levels": level, "net": net,
            "invested": invested, "is_stop": is_stop
        }
        trades.append(rec)
        if is_stop:
            stops.append(rec)

        peak_balance = max(peak_balance, balance)
        dd = (peak_balance - balance) / peak_balance * 100
        max_drawdown = max(max_drawdown, dd)

        in_trade   = False
        entries    = []
        level      = 0
        first_price = None
        avg_price  = 0.0
        total_qty  = 0.0
        invested   = 0.0

    for ts, o, high, low, close in candles:
        if insolvent:
            break

        # фандинг каждые 8 часов в позиции
        if in_trade:
            hour8 = ts // (8 * 3600 * 1000)
            if last_funding != hour8:
                last_funding = hour8
                pay = total_qty * avg_price * FUNDING_8H_PCT / 100
                balance -= pay

        if not in_trade:
            if recent_high is None or high > recent_high:
                recent_high = high
            trigger = recent_high * (1 - ENTRY_DROP_PCT / 100)
            if low <= trigger:
                entry_price = min(trigger, o)
                if open_level(entry_price, ts):
                    in_trade    = True
                    first_price = entry_price
                else:
                    insolvent = True
        else:
            # 1) стоп-лосс (пессимистично: проверяем раньше ТП)
            sl_price = first_price * (1 - STOP_LOSS_PCT / 100)
            if low <= sl_price:
                close_position(sl_price, ts, is_stop=True)
                recent_high = close
                continue

            # 2) усреднения (возможно несколько за свечу)
            while level < len(MARGINS):
                lvl_price = first_price * (1 - level * AVERAGING_STEP_PCT / 100)
                if low <= lvl_price:
                    if not open_level(lvl_price, ts):
                        insolvent = True
                        break
                else:
                    break

            # 3) тейк-профит от текущей средней
            tp_pct   = SMART_TP[min(max(level - 1, 0), len(SMART_TP) - 1)]
            tp_price = avg_price * (1 + tp_pct / 100)
            if high >= tp_price:
                close_position(tp_price, ts, is_stop=False)
                recent_high = close

    # незакрытая позиция в конце — считаем по последней цене
    open_pnl = 0.0
    if in_trade:
        last_close = candles[-1][4]
        open_pnl = total_qty * (last_close - avg_price)

    return {
        "balance":      balance,
        "open_pnl":     open_pnl,
        "invested_now": invested,
        "in_trade":     in_trade,
        "level_now":    level,
        "trades":       trades,
        "stops":        stops,
        "level_dist":   level_dist,
        "monthly":      monthly,
        "max_dd":       max_drawdown,
        "insolvent":    insolvent
    }


def report(res: dict) -> str:
    trades   = res["trades"]
    stops    = res["stops"]
    n        = len(trades)
    n_stop   = len(stops)
    n_win    = n - n_stop
    wr       = round(n_win / n * 100, 1) if n else 0
    total    = sum(t["net"] for t in trades)
    stop_l   = sum(t["net"] for t in stops)
    end_bal  = res["balance"] + res["invested_now"] + res["open_pnl"]

    lines = [
        f"📊 БЭКТЕСТ {SYMBOL} | {DAYS} дней | {INTERVAL}m свечи",
        f"Конфиг: {len(MARGINS)} ур. | шаг {AVERAGING_STEP_PCT}% | СЛ -{STOP_LOSS_PCT}%",
        "═" * 34,
        f"💰 Старт:        ${START_BALANCE:,.0f}",
        f"💳 Итог (экв.):  ${end_bal:,.2f}",
        f"📈 PnL закрытый: ${total:+,.2f}",
        f"✅ Сделок: {n} | ❌ Стопов: {n_stop} | 🏆 WR: {wr}%",
        f"💥 Убыток от стопов: ${stop_l:,.2f}",
        f"📉 Макс. просадка баланса: -{res['max_dd']:.1f}%",
    ]
    if res["in_trade"]:
        lines.append(
            f"⚠️ Открытая позиция в конце: ур.{res['level_now']}, "
            f"нереализ. PnL ${res['open_pnl']:+,.2f}"
        )
    if res["insolvent"]:
        lines.append("🚨 БАЛАНСА НЕ ХВАТИЛО на уровень — стратегия сломалась!")

    lines.append("─" * 34)
    lines.append("Макс. уровень за сделку:")
    for lvl in sorted(res["level_dist"]):
        cnt = res["level_dist"][lvl]
        bar = "█" * min(cnt, 30)
        lines.append(f"  ур.{lvl:>2}: {cnt:>4} {bar}")

    lines.append("─" * 34)
    lines.append("По месяцам:")
    for mk in sorted(res["monthly"]):
        lines.append(f"  {mk}: ${res['monthly'][mk]:+,.2f}")

    if stops:
        lines.append("─" * 34)
        lines.append("Даты стопов:")
        for s in stops:
            d = datetime.fromtimestamp(s["ts"] / 1000)
            lines.append(f"  {d:%d.%m.%Y} | ур.{s['levels']} | ${s['net']:,.2f}")

    return "\n".join(lines)


if __name__ == "__main__":
    candles = fetch_klines(DAYS, INTERVAL)
    res     = run_backtest(candles)
    text    = report(res)
    print(text)
    # Telegram ограничен 4096 символами
    tg_send(text[:4000])
    if len(text) > 4000:
        tg_send(text[4000:8000])
