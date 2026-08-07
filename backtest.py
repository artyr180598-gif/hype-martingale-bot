import time
import os
import requests
from datetime import datetime, timezone

# ── DEFAULTS (overridden from main when run via Telegram) ──────────
SYMBOL       = "HYPEUSDT"
DAYS         = 365
INTERVAL     = "15"
COMMISSION   = 0.1
SLIPPAGE     = 0.05
FUNDING_8H   = 0.01
START_BAL    = 1000.0
MMR          = 0.5    # maintenance margin rate Bybit %
LEVERAGE     = 10

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Strategy params — aligned with config.py defaults
MARGINS            = [40, 46, 53, 61, 70, 80, 92, 106, 122, 140]
ENTRY_DROP_PCT     = 0.6
AVERAGING_STEP_PCT = 1.2
STOP_LOSS_PCT      = 15.0
SMART_TP           = [0.7, 0.8, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]

# Multi-config comparison (standalone CLI)
CONFIGS = [
    {
        "name":     "A: 20x / 17ур / СЛ-29%",
        "leverage": 20,
        "margins":  [155, 170, 187, 206, 227, 250, 275,
                     302, 332, 365, 402, 442, 486, 535, 588, 647, 712],
        "step":     1.45,
        "sl":       29.0,
        "smart_tp": [0.7, 0.8, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0,
                     1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
        "pause_after_liq": False,
    },
    {
        "name":     "B: 6x / 17ур / СЛ-29%",
        "leverage": 6,
        "margins":  [155, 170, 187, 206, 227, 250, 275,
                     302, 332, 365, 402, 442, 486, 535, 588, 647, 712],
        "step":     1.45,
        "sl":       29.0,
        "smart_tp": [0.7, 0.8, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0,
                     1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
        "pause_after_liq": False,
    },
    {
        "name":     "C: 10x / 10ур / СЛ-12%",
        "leverage": 10,
        "margins":  [200, 225, 252, 283, 317, 355, 398, 446, 500, 560],
        "step":     1.2,
        "sl":       12.0,
        "smart_tp": [0.7, 0.8, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
        "pause_after_liq": False,
    },
    {
        "name":     "D: 6x / 17ур + пауза 48г після лікв.",
        "leverage": 6,
        "margins":  [155, 170, 187, 206, 227, 250, 275,
                     302, 332, 365, 402, 442, 486, 535, 588, 647, 712],
        "step":     1.45,
        "sl":       29.0,
        "smart_tp": [0.7, 0.8, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0,
                     1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
        "pause_after_liq": True,
    },
]


def tg(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(text)
        return
    for i in range(0, len(text), 3900):
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": text[i:i + 3900]},
                timeout=10,
            )
            time.sleep(0.5)
        except Exception as e:
            print(f"TG error: {e}")


def fetch_klines(days: int, interval: str = "15") -> list:
    """Download Bybit linear klines. Used by Telegram backtest button."""
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86400 * 1000
    out, cursor, empty = [], start_ms, 0
    iv_ms = int(interval) * 60 * 1000

    print(f"Завантажую {days} днів свічок {interval}m...")
    while cursor < end_ms:
        try:
            r = requests.get(
                "https://api.bybit.com/v5/market/kline",
                params={
                    "category": "linear",
                    "symbol": SYMBOL,
                    "interval": interval,
                    "start": cursor,
                    "limit": 1000,
                },
                timeout=15,
            ).json()
        except Exception as e:
            print(f"Помилка: {e}, повтор...")
            time.sleep(2)
            continue

        rows = r.get("result", {}).get("list", [])
        if not rows:
            empty += 1
            if empty > 3:
                break
            cursor += 500 * iv_ms
            continue
        empty = 0

        rows.sort(key=lambda x: int(x[0]))
        for row in rows:
            ts = int(row[0])
            if ts >= end_ms:
                continue
            out.append(
                (ts, float(row[1]), float(row[2]), float(row[3]), float(row[4]))
            )

        newest = int(rows[-1][0])
        if newest <= cursor:
            break
        cursor = newest + iv_ms
        print(f"  ...{len(out)} свічок", end="\r")
        time.sleep(0.05)

    seen, clean = set(), []
    for c in sorted(out, key=lambda x: x[0]):
        if c[0] not in seen:
            seen.add(c[0])
            clean.append(c)

    if not clean:
        print("\nНемає свічок")
        return []

    d1 = datetime.fromtimestamp(clean[0][0] / 1000)
    d2 = datetime.fromtimestamp(clean[-1][0] / 1000)
    print(f"\nГотово: {len(clean)} свічок | {d1:%d.%m.%Y}→{d2:%d.%m.%Y}")
    return clean


def fetch_candles() -> list:
    """CLI helper — uses module DAYS/INTERVAL."""
    return fetch_klines(DAYS, INTERVAL)


def run_config(cfg: dict, candles: list) -> dict:
    lev = cfg["leverage"]
    margins = cfg["margins"]
    step_pct = cfg["step"]
    sl_pct = cfg["sl"]
    smart_tp = cfg["smart_tp"]
    pause48 = cfg["pause_after_liq"]

    balance = START_BAL
    peak_eq = START_BAL
    max_dd = 0.0
    max_dd_ts = None

    in_trade = False
    entries = []
    level = 0
    first_price = None
    avg_price = 0.0
    total_qty = 0.0
    invested = 0.0
    recent_high = None
    last_fund = None
    pause_until = 0

    trades, liqs = [], []
    monthly, lev_dist = {}, {}
    total_funding = 0.0

    def mkey(ts):
        d = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        return f"{d.year}-{d.month:02d}"

    def liq_price():
        if not entries or total_qty == 0:
            return 0.0
        pos_val = total_qty * avg_price
        margin_ratio = invested - pos_val * MMR / 100
        return avg_price - margin_ratio / total_qty

    def open_lvl(price):
        nonlocal balance, level, avg_price, total_qty, invested
        if level >= len(margins):
            return False
        fill = price * (1 + SLIPPAGE / 100)
        margin = margins[level]
        qty = (margin * lev) / fill
        comm = qty * fill * COMMISSION / 100
        if margin + comm > balance:
            return False
        balance -= margin + comm
        entries.append((fill, qty, margin))
        total_qty += qty
        invested += margin
        avg_price = sum(p * q for p, q, _ in entries) / total_qty
        level += 1
        return True

    def close_pos(price, ts, is_liq=False, is_sl=False):
        nonlocal balance, in_trade, entries, level
        nonlocal first_price, avg_price, total_qty, invested

        fill = price * (1 - SLIPPAGE / 100)
        gross = total_qty * (fill - avg_price)
        comm = total_qty * fill * COMMISSION / 100
        net = gross - comm
        balance += invested + net

        mk = mkey(ts)
        monthly[mk] = monthly.get(mk, 0.0) + net
        lev_dist[level] = lev_dist.get(level, 0) + 1

        rec = {
            "ts": ts,
            "levels": level,
            "net": net,
            "is_liq": is_liq,
            "is_sl": is_sl,
        }
        trades.append(rec)
        if is_liq:
            liqs.append(rec)

        in_trade = False
        entries = []
        level = 0
        first_price = None
        avg_price = 0.0
        total_qty = 0.0
        invested = 0.0

    for ts, o, high, low, close in candles:
        if balance <= 0:
            break
        if pause48 and ts < pause_until:
            continue

        if in_trade:
            h8 = ts // (8 * 3600 * 1000)
            if last_fund != h8:
                last_fund = h8
                pay = total_qty * avg_price * FUNDING_8H / 100
                balance -= pay
                total_funding += pay

        if in_trade:
            worst = balance + invested + total_qty * (low - avg_price)
        else:
            worst = balance
        peak_eq = max(
            peak_eq,
            balance
            + invested
            + (total_qty * (close - avg_price) if in_trade else 0),
        )
        dd = (peak_eq - worst) / peak_eq * 100 if peak_eq > 0 else 0
        if dd > max_dd:
            max_dd, max_dd_ts = dd, ts

        if not in_trade:
            if recent_high is None or high > recent_high:
                recent_high = high
            trigger = recent_high * (1 - ENTRY_DROP_PCT / 100)
            if low <= trigger:
                ep = min(trigger, o)
                if open_lvl(ep):
                    in_trade = True
                    first_price = ep
        else:
            lp = liq_price()
            if lp > 0 and low <= lp:
                rec = {
                    "ts": ts,
                    "levels": level,
                    "net": -(invested),
                    "is_liq": True,
                    "is_sl": False,
                }
                mk = mkey(ts)
                monthly[mk] = monthly.get(mk, 0.0) - invested
                lev_dist[level] = lev_dist.get(level, 0) + 1
                liqs.append(rec)
                trades.append(rec)
                in_trade, entries, level = False, [], 0
                first_price = None
                avg_price = 0.0
                total_qty = invested = 0.0
                recent_high = close
                if pause48:
                    pause_until = ts + 48 * 3600 * 1000
                continue

            sl = first_price * (1 - sl_pct / 100)
            if low <= sl:
                close_pos(sl, ts, is_sl=True)
                recent_high = close
                continue

            while level < len(margins):
                lp2 = first_price * (1 - level * step_pct / 100)
                if low <= lp2:
                    if not open_lvl(lp2):
                        break
                else:
                    break

            tp_idx = min(max(level - 1, 0), len(smart_tp) - 1)
            tp = avg_price * (1 + smart_tp[tp_idx] / 100)
            if high >= tp:
                close_pos(tp, ts)
                recent_high = close

    open_pnl = total_qty * (candles[-1][4] - avg_price) if in_trade else 0.0
    end_eq = balance + invested + open_pnl

    return {
        "trades": trades,
        "liqs": liqs,
        "monthly": monthly,
        "lev_dist": lev_dist,
        "max_dd": max_dd,
        "max_dd_ts": max_dd_ts,
        "end_eq": end_eq,
        "balance": balance,
        "total_funding": total_funding,
        "period": (candles[0][0], candles[-1][0]),
        "leverage": lev,
        "margins": margins,
        "step": step_pct,
        "sl": sl_pct,
    }


def run_backtest(candles: list) -> dict:
    """Single-config backtest using current LEVERAGE/MARGINS (Telegram path)."""
    cfg = {
        "name": f"Bot config | {LEVERAGE}x | {len(MARGINS)} ур",
        "leverage": LEVERAGE,
        "margins": list(MARGINS),
        "step": AVERAGING_STEP_PCT,
        "sl": STOP_LOSS_PCT,
        "smart_tp": list(SMART_TP),
        "pause_after_liq": False,
    }
    return run_config(cfg, candles)


def report(res: dict) -> str:
    """Text report for Telegram (single config)."""
    tr = res["trades"]
    liqs = res["liqs"]
    n = len(tr)
    nl = len(liqs)
    wins = n - nl
    wr = round(wins / n * 100, 1) if n else 0
    total = sum(t["net"] for t in tr)
    end = res["end_eq"]
    pct = round((end - START_BAL) / START_BAL * 100, 1)
    d1 = datetime.fromtimestamp(res["period"][0] / 1000)
    d2 = datetime.fromtimestamp(res["period"][1] / 1000)
    lev = res.get("leverage", LEVERAGE)
    margins = res.get("margins", MARGINS)

    lines = [
        f"{'═' * 36}",
        f"🔬 БЭКТЕСТ HYPE",
        f"{'═' * 36}",
        f"📆 {d1:%d.%m.%Y} → {d2:%d.%m.%Y}",
        f"🕯 TF: {INTERVAL}m | ⚡ {lev}x | {len(margins)} ур",
        f"📉 Вход: -{ENTRY_DROP_PCT}% | шаг {res.get('step', AVERAGING_STEP_PCT)}%",
        f"🔴 СЛ: -{res.get('sl', STOP_LOSS_PCT)}%",
        f"{'─' * 36}",
        f"💰 ${START_BAL:,.0f} → ${end:,.2f} ({pct:+.1f}%)",
        f"📈 PnL закрыт.: ${total:+,.2f}",
        f"🌊 Фандинг:    -${res['total_funding']:,.2f}",
        f"✅ {wins} угод | 💀 {nl} лікв. | 🏆 WR {wr}%",
        f"📉 Макс. просадка: -{res['max_dd']:.1f}%",
    ]
    if res["max_dd_ts"]:
        lines.append(
            f"   дата: {datetime.fromtimestamp(res['max_dd_ts'] / 1000):%d.%m.%Y}"
        )

    if liqs:
        lines.append("💀 Ліквідації:")
        for lq in liqs[:5]:
            d = datetime.fromtimestamp(lq["ts"] / 1000)
            lines.append(f"  {d:%d.%m.%Y} ур.{lq['levels']} ${lq['net']:,.0f}")
        if len(liqs) > 5:
            lines.append(f"  ...ще {len(liqs) - 5}")

    lines.append("📅 По місяцях:")
    for mk in sorted(res["monthly"])[-6:]:
        lines.append(f"  {mk}: ${res['monthly'][mk]:+,.0f}")

    lines.append(f"{'═' * 36}")
    return "\n".join(lines)


def report_config(cfg: dict, res: dict) -> str:
    tr = res["trades"]
    liqs = res["liqs"]
    n = len(tr)
    nl = len(liqs)
    wins = n - nl
    wr = round(wins / n * 100, 1) if n else 0
    total = sum(t["net"] for t in tr)
    end = res["end_eq"]
    pct = round((end - START_BAL) / START_BAL * 100, 1)

    lines = [
        f"{'═' * 36}",
        f"📊 {cfg['name']}",
        f"{'═' * 36}",
        f"💰 ${START_BAL:,.0f} → ${end:,.2f} ({pct:+.1f}%)",
        f"📈 PnL закрит.: ${total:+,.2f}",
        f"🌊 Фандинг:    -${res['total_funding']:,.2f}",
        f"✅ {wins} угод | 💀 {nl} лікв. | 🏆 WR {wr}%",
        f"📉 Макс. просадка: -{res['max_dd']:.1f}%",
    ]
    if res["max_dd_ts"]:
        lines.append(
            f"   дата: {datetime.fromtimestamp(res['max_dd_ts'] / 1000):%d.%m.%Y}"
        )

    if liqs:
        lines.append("💀 Ліквідації:")
        for lq in liqs[:5]:
            d = datetime.fromtimestamp(lq["ts"] / 1000)
            lines.append(f"  {d:%d.%m.%Y} ур.{lq['levels']} ${lq['net']:,.0f}")
        if len(liqs) > 5:
            lines.append(f"  ...ще {len(liqs) - 5}")

    lines.append("📅 По місяцях:")
    for mk in sorted(res["monthly"])[-6:]:
        lines.append(f"  {mk}: ${res['monthly'][mk]:+,.0f}")

    return "\n".join(lines)


def summary(results: list) -> str:
    lines = [
        "🏆 ПІДСУМОК — ЯКИЙ КОНФІГ ВИГРАВ?",
        "═" * 36,
    ]
    ranked = sorted(results, key=lambda x: x[1]["end_eq"], reverse=True)
    medals = ["🥇", "🥈", "🥉", "4️⃣"]
    for i, (cfg, res) in enumerate(ranked):
        pct = round((res["end_eq"] - START_BAL) / START_BAL * 100, 1)
        nl = len(res["liqs"])
        lines.append(
            f"{medals[i]} {cfg['name']}\n"
            f"   Підсумок: ${res['end_eq']:,.0f} ({pct:+.1f}%)"
            f" | Ліквід.: {nl}"
        )
    lines.append("═" * 36)
    winner = ranked[0]
    lines.append(
        f"✅ ПЕРЕМОЖЕЦЬ: {winner[0]['name']}\n"
        f"   Саме цей конфіг рекомендується для реалу!"
    )
    return "\n".join(lines)


if __name__ == "__main__":
    tg("⏳ Запускаю мульти-бектест 4 конфігурацій...\nЗавантажую свічки...")
    candles = fetch_candles()

    if not candles:
        tg("🚨 Не вдалося завантажити свічки!")
    else:
        d1 = datetime.fromtimestamp(candles[0][0] / 1000)
        d2 = datetime.fromtimestamp(candles[-1][0] / 1000)
        tg(
            f"✅ Завантажено {len(candles)} свічок\n"
            f"Період: {d1:%d.%m.%Y} → {d2:%d.%m.%Y}\n"
            f"Тестую 4 конфігурації..."
        )

        results = []
        for cfg in CONFIGS:
            print(f"\nТестую {cfg['name']}...")
            res = run_config(cfg, candles)
            results.append((cfg, res))
            tg(report_config(cfg, res))
            time.sleep(1)

        tg(summary(results))
        tg("✅ Бектест завершено!")
