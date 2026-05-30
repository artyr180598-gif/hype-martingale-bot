import time
import logging
import sys
import os
import requests
import threading
import json
from datetime import datetime
from config import (
    SYMBOL, LEVERAGE, MARGINS,
    ENTRY_DROP_PCT, AVERAGING_STEP_PCT,
    STOP_LOSS_PCT, STOP_LOSS_BACKUP_PCT,
    COMMISSION_PCT, SMART_TP,
    CHECK_INTERVAL, HEARTBEAT_MINUTES,
    QTY_PRECISION, PRICE_PRECISION,
    DEMO_MODE, DEMO_BALANCE,
    BOT_VERSION,
    STATS_FILE, STATE_FILE, HISTORY_FILE,
    HISTORY_PAGE_SIZE
)
from bybit_client import BybitClient
from demo_client import DemoClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8")
    ]
)
logger = logging.getLogger(__name__)


def _atomic_write(filepath: str, data: dict):
    tmp = filepath + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, filepath)


def _load_json(filepath: str, default: dict) -> dict:
    try:
        if os.path.exists(filepath):
            with open(filepath, encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Ошибка загрузки {filepath}: {e}")
    return default


def load_stats() -> dict:
    return _load_json(STATS_FILE, {
        "total_profit":   0.0,
        "total_trades":   0,
        "total_stops":    0,
        "today_profit":   0.0,
        "today_trades":   0,
        "today_date":     str(datetime.now().date()),
        "session_profit": 0.0,
        "session_trades": 0,
    })


def save_stats(s): _atomic_write(STATS_FILE, s)
def load_state() -> dict: return _load_json(STATE_FILE, {})
def save_state(s): _atomic_write(STATE_FILE, s)


def load_history() -> dict:
    return _load_json(HISTORY_FILE, {
        "sessions":   [],
        "all_trades": [],
        "summary": {
            "total_profit":     0.0,
            "total_trades":     0,
            "total_stops":      0,
            "total_commission": 0.0,
            "total_funding":    0.0,
            "first_start":      None
        }
    })


def save_history(h): _atomic_write(HISTORY_FILE, h)


class MartingaleBot:

    def __init__(self):
        self.demo_mode      = DEMO_MODE
        self.bybit          = DemoClient(DEMO_BALANCE) if DEMO_MODE else BybitClient()
        self.running        = True
        self.paused         = False
        self.stats          = load_stats()
        self.history        = load_history()
        self.start_time     = datetime.now()
        self.last_heartbeat = datetime.now()
        self.history_page   = {}

        self._init_session()

        if DEMO_MODE:
            self._sync_stats_with_balance()

        self._reset()
        self._restore_state()

    def _sync_stats_with_balance(self):
        b = self.bybit.get_balance_info()
        logger.info(
            f"🔄 Синхронизация: PnL=${b['pnl']:.2f} | "
            f"комиссии=${b['commission']:.2f} | "
            f"фандинг=${b['funding']:.4f}"
        )
        self.stats["total_profit"] = b["pnl"]
        save_stats(self.stats)
        self.history["summary"]["total_profit"]     = b["pnl"]
        self.history["summary"]["total_commission"] = b["commission"]
        self.history["summary"]["total_funding"]    = b["funding"]
        save_history(self.history)

    def _init_session(self):
        if not self.history["summary"]["first_start"]:
            self.history["summary"]["first_start"] = datetime.now().isoformat()
        session_num = len(self.history["sessions"]) + 1
        self.current_session = {
            "session_id":   session_num,
            "version":      BOT_VERSION,
            "mode":         "DEMO" if DEMO_MODE else "REAL",
            "start":        datetime.now().isoformat(),
            "trades":       [],
            "profit":       0.0,
            "trades_count": 0,
            "stops_count":  0,
            "commission":   0.0,
            "funding":      0.0,
            "label": (
                f"Сессия #{session_num} | "
                f"v{BOT_VERSION} | "
                f"{datetime.now().strftime('%d.%m.%Y %H:%M')}"
            )
        }
        self.history["sessions"].append(self.current_session)
        save_history(self.history)
        logger.info(f"📂 Сессия #{session_num} начата")

    def _add_trade_to_history(
        self, profit, is_stop, levels,
        entry_price, exit_price, invested,
        commission, funding=0.0
    ):
        now   = datetime.now()
        trade = {
            "session_id":  self.current_session["session_id"],
            "date":        now.strftime("%d.%m.%Y"),
            "time":        now.strftime("%H:%M:%S"),
            "datetime":    now.isoformat(),
            "type":        "STOP" if is_stop else "TP",
            "levels":      levels,
            "entry_price": round(entry_price, 3),
            "exit_price":  round(exit_price, 3),
            "invested":    round(invested, 2),
            "commission":  round(commission, 3),
            "funding":     round(funding, 4),
            "profit":      round(profit if not is_stop else -abs(profit), 2),
            "mode":        "DEMO" if self.demo_mode else "REAL"
        }
        self.history["all_trades"].append(trade)
        self.current_session["trades"].append(trade)
        self.current_session["commission"] += commission
        self.current_session["funding"]    += funding

        if is_stop:
            self.current_session["stops_count"]     += 1
            self.current_session["profit"]          -= abs(profit)
            self.history["summary"]["total_stops"]  += 1
            self.history["summary"]["total_profit"] -= abs(profit)
        else:
            self.current_session["trades_count"]    += 1
            self.current_session["profit"]          += profit
            self.history["summary"]["total_profit"] += profit

        self.history["summary"]["total_trades"]     += 1
        self.history["summary"]["total_commission"] += commission
        self.history["summary"]["total_funding"]    += funding
        save_history(self.history)

    def _reset(self):
        self.in_trade          = False
        self.entries           = []
        self.current_level     = 0
        self.first_entry_price = None
        self.average_price     = None
        self.total_qty         = 0.0
        self.recent_high       = None
        save_state({})
        logger.info("🔄 Сброс — ожидание входа")

    def _restore_state(self):
        state = load_state()
        if not state or not state.get("in_trade"):
            return

        if not self.demo_mode:
            if self.bybit.get_position_size(SYMBOL) == 0:
                logger.warning("⚠️ Позиции на бирже нет — сбрасываем")
                save_state({})
                return

        self.in_trade          = state.get("in_trade", False)
        self.entries           = [tuple(e) for e in state.get("entries", [])]
        self.current_level     = state.get("current_level", 0)
        self.first_entry_price = state.get("first_entry_price")
        self.average_price     = state.get("average_price")
        self.total_qty         = state.get("total_qty", 0.0)
        self.recent_high       = state.get("recent_high")

        if self.demo_mode and self.in_trade and self.entries:
            self.bybit.position_size   = self.total_qty
            self.bybit.avg_entry_price = self.average_price or 0.0
            self.bybit.total_invested  = self._total_invested()
            if self.average_price:
                tp_pct   = self._get_tp_pct()
                tp_price = self._round_price(
                    self.average_price * (1 + tp_pct / 100)
                )
                self.bybit.tp_price = tp_price
                logger.info(f"♻️ Демо TP восстановлен @ ${tp_price}")

        if not self.demo_mode and self.in_trade and self.average_price:
            tp_pct   = self._get_tp_pct()
            tp_price = self._round_price(
                self.average_price * (1 + tp_pct / 100)
            )
            self.bybit.set_take_profit(SYMBOL, tp_price)
            logger.info(f"♻️ Реальный TP переустановлен @ ${tp_price}")

        logger.info(
            f"♻️ Восстановлено | Ур.{self.current_level} | "
            f"Ср.${self.average_price} | {self.total_qty} HYPE"
        )

    def _save_state(self):
        save_state({
            "in_trade":          self.in_trade,
            "entries":           [list(e) for e in self.entries],
            "current_level":     self.current_level,
            "first_entry_price": self.first_entry_price,
            "average_price":     self.average_price,
            "total_qty":         self.total_qty,
            "recent_high":       self.recent_high
        })

    def _round_qty(self, q):   return round(q, QTY_PRECISION)
    def _round_price(self, p): return round(p, PRICE_PRECISION)
    def _total_invested(self): return sum(m for _, _, m in self.entries)

    def _get_tp_pct(self):
        idx = min(max(self.current_level - 1, 0), len(SMART_TP) - 1)
        return SMART_TP[idx]

    def _calc_average(self):
        cost = sum(p * q for p, q, _ in self.entries)
        qty  = sum(q for _, q, _ in self.entries)
        return cost / qty if qty else 0.0

    def _drop_pct(self, price):
        if not self.first_entry_price:
            return 0.0
        return (self.first_entry_price - price) / self.first_entry_price * 100

    def _calc_commission(self, qty, price):
        return round(qty * price * COMMISSION_PCT / 100, 3)

    def _check_new_day(self):
        today = str(datetime.now().date())
        if self.stats["today_date"] != today:
            self.stats.update({
                "today_date":   today,
                "today_profit": 0.0,
                "today_trades": 0
            })
            save_stats(self.stats)

    def _uptime(self):
        d   = datetime.now() - self.start_time
        tot = int(d.total_seconds())
        return f"{tot//3600}ч {(tot%3600)//60}м"

    def _mode_label(self):
        return "🎮 ДЕМО" if self.demo_mode else "💰 РЕАЛ"

    def _get_chat_id(self):
        from config import TELEGRAM_CHAT_ID
        return TELEGRAM_CHAT_ID

    def _should_enter(self, price):
        if self.recent_high is None:
            return False
        return (self.recent_high - price) / self.recent_high * 100 >= ENTRY_DROP_PCT

    def _should_average(self, price):
        if not self.in_trade or self.current_level >= len(MARGINS):
            return False
        return self._drop_pct(price) >= self.current_level * AVERAGING_STEP_PCT

    def _should_stop(self, price):
        return self.in_trade and self._drop_pct(price) >= STOP_LOSS_PCT

    def _open_level(self, price) -> tuple[bool, float]:
        if self.current_level >= len(MARGINS):
            return False, 0.0
        margin       = MARGINS[self.current_level]
        expected_qty = self._round_qty((margin * LEVERAGE) / price)
        result       = self.bybit.place_market_buy(SYMBOL, expected_qty)
        if not result:
            return False, 0.0
        actual_price = result.get("avg_price", price)
        actual_qty   = result.get("qty", expected_qty)
        self.entries.append((actual_price, actual_qty, margin))
        self.total_qty     = self._round_qty(sum(q for _, q, _ in self.entries))
        self.average_price = self._round_price(self._calc_average())
        self.current_level += 1
        self._save_state()
        logger.info(
            f"📊 Ур.{self.current_level}: "
            f"{actual_qty} HYPE @ ${actual_price:.3f} | "
            f"Ср: ${self.average_price:.3f}"
        )
        return True, actual_price

    def _update_tp(self) -> tuple[float | None, float]:
        tp_pct   = self._get_tp_pct()
        tp_price = self._round_price(self.average_price * (1 + tp_pct / 100))
        if not self.bybit.set_take_profit(SYMBOL, tp_price):
            return None, tp_pct
        self._save_state()
        return tp_price, tp_pct

    def _check_tp_hit(self, price) -> tuple[bool, float, float]:
        if not self.in_trade:
            return False, 0.0, 0.0
        if self.demo_mode:
            hit, tp_price = self.bybit.check_tp_triggered(price)
            if hit:
                pnl_data = self.bybit.get_closed_pnl(SYMBOL)
                profit   = pnl_data["pnl"] if pnl_data else round(
                    self.total_qty * (tp_price - self.average_price)
                    - self._calc_commission(self.total_qty, tp_price), 2
                )
                return True, tp_price, profit
            return False, 0.0, 0.0
        else:
            if self.bybit.get_position_size(SYMBOL) == 0.0:
                pnl_data = self.bybit.get_closed_pnl(SYMBOL)
                if pnl_data:
                    return True, pnl_data["exit_price"], pnl_data["pnl"]
                return True, price, round(
                    self._total_invested() * LEVERAGE * self._get_tp_pct() / 100, 2
                )
            return False, 0.0, 0.0

    def _close_all_stop(self, stop_price) -> float:
        self.bybit.cancel_all_orders(SYMBOL)
        self.bybit.market_close_all(SYMBOL, self.total_qty)
        time.sleep(0.5)
        pnl_data = self.bybit.get_closed_pnl(SYMBOL)
        if pnl_data:
            return round(abs(pnl_data["pnl"]), 2)
        commission = self._calc_commission(self.total_qty, stop_price)
        loss = self.total_qty * (self.average_price - stop_price) + commission
        return round(max(loss, 0), 2)

    def _record_trade(
        self, profit, is_stop, levels, now,
        exit_price=0.0, commission=0.0, funding=0.0
    ):
        self.stats["total_trades"]   += 1
        self.stats["today_trades"]   += 1
        self.stats["session_trades"] += 1
        if is_stop:
            self.stats["total_stops"]    += 1
            self.stats["total_profit"]   -= abs(profit)
            self.stats["today_profit"]   -= abs(profit)
            self.stats["session_profit"] -= abs(profit)
        else:
            self.stats["total_profit"]   += profit
            self.stats["today_profit"]   += profit
            self.stats["session_profit"] += profit
        save_stats(self.stats)
        self._add_trade_to_history(
            profit      = profit,
            is_stop     = is_stop,
            levels      = levels,
            entry_price = self.first_entry_price or 0.0,
            exit_price  = exit_price,
            invested    = self._total_invested(),
            commission  = commission,
            funding     = funding
        )

    def _balance_block(self) -> str:
        if self.demo_mode:
            b = self.bybit.get_balance_info()
            fs = (
                f"║ 🌊 Фандинг:  -${b['funding']:,.4f}\n"
                if b["funding"] > 0 else ""
            )
            return (
                f"║ 💳 Баланс:   ${b['balance']:>12,.2f}\n"
                f"║ 💵 Доступно: ${b['available']:>12,.2f}\n"
                f"║ 📊 PnL:      ${b['pnl']:>+12,.2f} ({b['pnl_pct']:+.2f}%)\n"
                f"║ 💸 Комиссии:${b['commission']:>12,.2f}\n"
                f"{fs}"
            )
        else:
            wb = self.bybit.get_wallet_balance()
            return (
                f"║ 💳 Баланс:   ${wb['balance']:>12,.2f}\n"
                f"║ 💵 Доступно: ${wb['available']:>12,.2f}\n"
            )

    def get_dashboard(self) -> str:
        price = self.bybit.get_price(SYMBOL)
        if not price:
            return "⚠️ Ошибка получения цены"
        self._check_new_day()
        total = self.stats["total_trades"]
        wins  = total - self.stats["total_stops"]
        wr    = round(wins / total * 100) if total else 0

        if not self.in_trade:
            drop = round(
                (self.recent_high - price) / self.recent_high * 100, 2
            ) if self.recent_high else 0.0
            need = round(max(ENTRY_DROP_PCT - drop, 0), 2)
            return (
                f"╔══════════════════════════════╗\n"
                f"║  🚀 HYPE BOT {self._mode_label()}\n"
                f"╠══════════════════════════════╣\n"
                f"║ 💲 Цена:      ${price:,.3f}\n"
                f"║ 📈 Максимум:  ${self.recent_high or '—'}\n"
                f"║ 📉 Откат:     -{drop}%\n"
                f"║ 🎯 До входа:  -{need}%\n"
                f"╠══════════════════════════════╣\n"
                f"{self._balance_block()}"
                f"╠══════════════════════════════╣\n"
                f"║ 📊 СТАТИСТИКА\n"
                f"║ ✅ Сегодня:  {self.stats['today_trades']} сд | +${self.stats['today_profit']:,.2f}\n"
                f"║ 📈 Сессия:   {self.stats['session_trades']} сд | +${self.stats['session_profit']:,.2f}\n"
                f"║ ❌ Стопов:   {self.stats['total_stops']}\n"
                f"║ 🏆 Винрейт:  {wr}%\n"
                f"╠══════════════════════════════╣\n"
                f"║ ⚡ {LEVERAGE}x | 🔄 {CHECK_INTERVAL}с | ⏱ {self._uptime()}\n"
                f"╚══════════════════════════════╝"
            )
        else:
            drop     = round(self._drop_pct(price), 2)
            tp_pct   = self._get_tp_pct()
            tp_price = self._round_price(self.average_price * (1 + tp_pct / 100))
            sl_price = self._round_price(self.first_entry_price * (1 - STOP_LOSS_PCT / 100))
            pnl      = round((price - self.average_price) * self.total_qty, 2)
            emoji    = "📈" if pnl >= 0 else "📉"
            return (
                f"╔══════════════════════════════╗\n"
                f"║  🚀 HYPE BOT {self._mode_label()}\n"
                f"╠══════════════════════════════╣\n"
                f"║ 🟢 АКТИВНАЯ СДЕЛКА\n"
                f"║ 💲 Цена:      ${price:,.3f}\n"
                f"║ 📦 HYPE:      {self.total_qty:,.2f}\n"
                f"║ 📈 Средняя:   ${self.average_price:,.3f}\n"
                f"║ {emoji} PnL:        ${pnl:,.2f}\n"
                f"║ 📉 Падение:   -{drop}%\n"
                f"╠══════════════════════════════╣\n"
                f"║ 💵 Вложено:   ${self._total_invested():,.2f}\n"
                f"║ 🎯 ТП:        ${tp_price:,.3f} (+{tp_pct}%)\n"
                f"║ 🔴 СЛ:        ${sl_price:,.3f} (-{STOP_LOSS_PCT}%)\n"
                f"║ 📊 Уровень:   {len(self.entries)}/{len(MARGINS)}\n"
                f"╠══════════════════════════════╣\n"
                f"{self._balance_block()}"
                f"║ ⚡ {LEVERAGE}x | ⏱ {self._uptime()}\n"
                f"╚══════════════════════════════╝"
            )

    def get_history_page(self, chat_id: str, page: int) -> tuple[str, dict]:
        all_trades  = self.history.get("all_trades", [])
        total       = len(all_trades)
        total_pages = max(1, (total + HISTORY_PAGE_SIZE - 1) // HISTORY_PAGE_SIZE)
        page        = max(0, min(page, total_pages - 1))
        self.history_page[chat_id] = page

        if self.demo_mode:
            b            = self.bybit.get_balance_info()
            real_profit  = b["pnl"]
            real_comm    = b["commission"]
            real_funding = b["funding"]
        else:
            s            = self.history["summary"]
            real_profit  = s["total_profit"]
            real_comm    = s["total_commission"]
            real_funding = s["total_funding"]

        s    = self.history["summary"]
        text = (
            f"📋 ИСТОРИЯ СДЕЛОК {self._mode_label()}\n"
            f"{'═'*34}\n"
            f"💰 PnL всего:  +${real_profit:,.2f}\n"
            f"💸 Комиссии:   ${real_comm:,.2f}\n"
            f"🌊 Фандинг:   -${real_funding:,.4f}\n"
            f"✅ Сделок: {s['total_trades']} | ❌ Стопов: {s['total_stops']}\n"
            f"{'─'*34}\n"
            f"📄 Стр. {page+1}/{total_pages} ({total} сделок)\n"
            f"{'─'*34}\n\n"
        )

        if total == 0:
            text += "Сделок пока нет 👀"
        else:
            reversed_trades = list(reversed(all_trades))
            start           = page * HISTORY_PAGE_SIZE
            end             = min(start + HISTORY_PAGE_SIZE, total)
            for t in reversed_trades[start:end]:
                emoji = "✅" if t["profit"] >= 0 else "❌"
                fs    = (
                    f" | 🌊${t['funding']:.4f}"
                    if t.get("funding", 0) > 0 else ""
                )
                text += (
                    f"{emoji} {t['date']} {t['time']}\n"
                    f"   Ур:{t['levels']} | "
                    f"${t['entry_price']:.3f}→${t['exit_price']:.3f}\n"
                    f"   💵${t['invested']:.0f} | "
                    f"💸${t['commission']:.3f}{fs}\n"
                    f"   💰 {'+' if t['profit']>=0 else ''}${t['profit']:,.2f}\n\n"
                )

        nav = []
        if page > 0:
            nav.append({"text": "◀️ Назад", "callback_data": f"hist_{page-1}"})
        nav.append({"text": f"📄 {page+1}/{total_pages}", "callback_data": "hist_noop"})
        if page < total_pages - 1:
            nav.append({"text": "Вперёд ▶️", "callback_data": f"hist_{page+1}"})

        keyboard = {
            "inline_keyboard": [
                nav,
                [
                    {"text": "📊 Статус", "callback_data": "status"},
                    {"text": "📈 Итоги",  "callback_data": "report"}
                ],
                [{"text": "⏹ Стоп бот", "callback_data": "stop"}]
            ]
        }
        return text, keyboard

    def get_daily_report(self) -> str:
        self._check_new_day()
        total = self.stats["total_trades"]
        wins  = total - self.stats["total_stops"]
        wr    = round(wins / total * 100) if total else 0

        if self.demo_mode:
            b   = self.bybit.get_balance_info()
            bal = (
                f"💳 Баланс: ${b['balance']:,.2f}\n"
                f"📊 PnL: ${b['pnl']:+,.2f} ({b['pnl_pct']:+.2f}%)\n"
                f"💸 Комиссий: ${b['commission']:,.2f}\n"
                f"🌊 Фандинг: -${b['funding']:,.4f}\n\n"
            )
        else:
            wb  = self.bybit.get_wallet_balance()
            bal = (
                f"💳 Баланс: ${wb['balance']:,.2f}\n"
                f"💵 Доступно: ${wb['available']:,.2f}\n\n"
            )

        return (
            f"📊 ИТОГИ ДНЯ {self._mode_label()}\n"
            f"{datetime.now().strftime('%d.%m.%Y')}\n\n"
            f"{bal}"
            f"✅ Сегодня: {self.stats['today_trades']} сд | +${self.stats['today_profit']:,.2f}\n"
            f"📈 Сессия:  {self.stats['session_trades']} сд | +${self.stats['session_profit']:,.2f}\n\n"
            f"✅ Сделок в журнале: {total} | ❌ Стопов: {self.stats['total_stops']}\n"
            f"🏆 Винрейт: {wr}%\n"
            f"⚡ Плечо: {LEVERAGE}x\n"
            f"⏱ Аптайм: {self._uptime()}"
        )

    def _tg_url(self, method: str) -> str:
        from config import TELEGRAM_TOKEN
        return f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"

    def send_keyboard(self, chat_id: str, text: str, keyboard: dict = None):
        if keyboard is None:
            keyboard = {
                "inline_keyboard": [
                    [
                        {"text": "📊 Статус",  "callback_data": "status"},
                        {"text": "💲 Цена",    "callback_data": "price"}
                    ],
                    [
                        {"text": "📋 История", "callback_data": "history"},
                        {"text": "📈 Итоги",   "callback_data": "report"}
                    ],
                    [{"text": "⏸ Пауза", "callback_data": "stop"}]
                ]
            }
        try:
            requests.post(
                self._tg_url("sendMessage"),
                json={"chat_id": chat_id, "text": text, "reply_markup": keyboard},
                timeout=10
            )
        except Exception as e:
            logger.error(f"send_keyboard: {e}")

    def send_history_page(self, chat_id: str, page: int):
        text, keyboard = self.get_history_page(chat_id, page)
        self.send_keyboard(chat_id, text, keyboard)

    def _answer_cb(self, cb_id: str):
        try:
            requests.post(
                self._tg_url("answerCallbackQuery"),
                json={"callback_query_id": cb_id},
                timeout=5
            )
        except:
            pass

    def poll_telegram(self):
        try:
            requests.get(
                self._tg_url("deleteWebhook"),
                params={"drop_pending_updates": True},
                timeout=10
            )
            logger.info("✅ Webhook удалён, polling активен")
        except:
            pass

        url, offset = self._tg_url("getUpdates"), 0
        while self.running:
            try:
                resp = requests.get(
                    url,
                    params={"timeout": 20, "offset": offset},
                    timeout=25
                ).json()
                for upd in resp.get("result", []):
                    offset = upd["update_id"] + 1
                    msg    = upd.get("message", {})
                    if msg.get("text"):
                        self._handle_cmd(msg["text"], str(msg["chat"]["id"]))
                    cb = upd.get("callback_query")
                    if cb:
                        self._answer_cb(cb["id"])
                        self._handle_cmd(
                            "/" + cb["data"],
                            str(cb["message"]["chat"]["id"])
                        )
            except Exception as e:
                logger.error(f"poll_telegram: {e}")
                time.sleep(5)

    def _handle_cmd(self, cmd: str, chat_id: str):
        if cmd in ("/start", "/status"):
            if self.paused:
                status_text = (
                    f"⏸ БОТ НА ПАУЗЕ\n\n"
                    f"Позиция сохранена:\n"
                    f"Уровень: {len(self.entries)}\n"
                    f"Средняя: ${self.average_price or '—'}\n"
                    f"Баланс: ${self.bybit.balance:,.2f}\n\n"
                    f"Нажми кнопку ниже чтобы продолжить"
                )
                keyboard = {
                    "inline_keyboard": [
                        [{"text": "▶️ Возобновить", "callback_data": "resume"}],
                        [
                            {"text": "📊 Статус", "callback_data": "status"},
                            {"text": "📈 Итоги", "callback_data": "report"}
                        ],
                        [{"text": "📋 История", "callback_data": "history"}]
                    ]
                }
                self.send_keyboard(chat_id, status_text, keyboard)
            else:
                self.send_keyboard(chat_id, self.get_dashboard())

        elif cmd == "/price":
            p = self.bybit.get_price(SYMBOL)
            self.send_keyboard(chat_id, f"💲 HYPE: ${p:,.3f}")

        elif cmd == "/history":
            self.send_history_page(chat_id, 0)

        elif cmd.startswith("/hist_"):
            try:
                self.send_history_page(chat_id, int(cmd.split("_")[1]))
            except:
                self.send_history_page(chat_id, 0)

        elif cmd == "/hist_noop":
            pass

        elif cmd == "/report":
            self.send_keyboard(chat_id, self.get_daily_report())

        elif cmd == "/stop":
            self.paused = True
            b = self.bybit.get_balance_info()
            self.send_keyboard(
                chat_id,
                f"⏸ БОТ НА ПАУЗЕ\n\n"
                f"Позиция сохранена ✅\n"
                f"Баланс: ${b['balance']:,.2f}\n"
                f"PnL: ${b['pnl']:+,.2f}\n"
                f"Уровень: {len(self.entries)}/{len(MARGINS)}\n\n"
                f"Нажми кнопку чтобы продолжить",
                {
                    "inline_keyboard": [
                        [
                            {
                                "text": "▶️ Возобновить бота",
                                "callback_data": "resume"
                            }
                        ],
                        [
                            {"text": "📊 Статус", "callback_data": "status"},
                            {"text": "📈 Итоги", "callback_data": "report"}
                        ],
                        [
                            {"text": "📋 История", "callback_data": "history"}
                        ]
                    ]
                }
            )
            logger.info("⏸ Бот на паузе")

        elif cmd == "/resume":
            if self.paused:
                self.paused = False
                self.send_keyboard(
                    chat_id,
                    "▶️ БОТ ВОЗОБНОВЛЁН\n\n"
                    "Продолжаю торговлю... 👀"
                )
                logger.info("▶️ Бот возобновлён")
            else:
                self.send_keyboard(chat_id, "✅ Бот уже работает")

    def _heartbeat(self):
        diff = (datetime.now() - self.last_heartbeat).total_seconds() / 60
        if diff < HEARTBEAT_MINUTES:
            return
        self.last_heartbeat = datetime.now()
        price   = self.bybit.get_price(SYMBOL)
        status  = "🟢 В позиции" if self.in_trade else "👀 Жду входа"
        if self.paused:
            status = "⏸ НА ПАУЗЕ"
        bal_str = ""
        if self.demo_mode:
            b       = self.bybit.get_balance_info()
            bal_str = f"\n💳 Баланс: ${b['balance']:,.2f} | PnL: ${b['pnl']:+,.2f}"
        self.send_keyboard(
            self._get_chat_id(),
            f"💓 БОТ АКТИВЕН\n\n"
            f"⚡ Статус: {status}\n"
            f"💲 HYPE: ${price:,.3f}\n"
            f"⏱ Аптайм: {self._uptime()}"
            f"{bal_str}\n"
            f"✅ {self.stats['total_trades']} сд | ❌ {self.stats['total_stops']} стопов"
        )

    def _check_funding(self):
        if not self.in_trade or self.paused:
            return
        result = self.bybit.apply_funding(SYMBOL)
        if not result:
            return
        payment = result.get("payment", 0)
        rate    = result.get("rate", 0)
        if not payment or payment <= 0.0001:
            return

        self.history["summary"]["total_funding"] += payment
        save_history(self.history)
        logger.info(f"🌊 Фандинг: ${payment:.4f} | ставка: {rate*100:.4f}%")

        if self.demo_mode:
            b = self.bybit.get_balance_info()
            self.send_keyboard(
                self._get_chat_id(),
                f"🌊 ФАНДИНГ СПИСАН\n\n"
                f"⏰ Время: {result['hour']:02d}:00 UTC\n"
                f"📊 Ставка: {rate*100:.4f}%\n"
                f"💸 Списано: -${payment:.4f}\n"
                f"📦 Позиция: {self.total_qty:,.2f} HYPE\n"
                f"💳 Баланс: ${b['balance']:,.2f}\n"
                f"📊 PnL: ${b['pnl']:+,.2f}"
            )

    def run(self):
        logger.info(
            f"🚀 ЗАПУСК v{BOT_VERSION} | "
            f"{'ДЕМО' if self.demo_mode else 'РЕАЛ'} | "
            f"Плечо {LEVERAGE}x | Интервал {CHECK_INTERVAL}с"
        )

        threading.Thread(target=self.poll_telegram, daemon=True).start()
        chat_id  = self._get_chat_id()
        setup    = self.bybit.auto_setup(SYMBOL, LEVERAGE)
        sess_num = self.current_session["session_id"]

        demo_note = ""
        if self.demo_mode:
            b         = self.bybit.get_balance_info()
            demo_note = (
                f"\n\n🎮 ДЕМО\n"
                f"💳 Баланс: ${b['balance']:,.2f}\n"
                f"📊 PnL: ${b['pnl']:+,.2f} ({b['pnl_pct']:+.2f}%)"
            )

        restored = "\n♻️ Состояние восстановлено!" if self.in_trade else ""

        self.send_keyboard(
            chat_id,
            f"🚀 BlackHorn Capital v{BOT_VERSION}\n"
            f"📂 Сессия #{sess_num}\n\n"
            f"⚙️ {setup.get('mode','?')} | {setup.get('leverage','?')}\n"
            f"📊 {SYMBOL} | ⚡ {LEVERAGE}x\n"
            f"📈 Уровней: {len(MARGINS)}\n"
            f"🎯 SmartTP: {SMART_TP}\n"
            f"📉 Вход: -{ENTRY_DROP_PCT}% | Стоп: -{STOP_LOSS_PCT}%\n"
            f"🔄 Проверка: каждые {CHECK_INTERVAL}с"
            f"{demo_note}{restored}\n\n"
            "👀 Слежу за HYPE..."
        )

        last_report = str(datetime.now().date())

        while self.running:
            try:
                # ← ПАУЗУ ПРОВЕРЯЕМ ЗДЕСЬ
                if self.paused:
                    time.sleep(2)
                    continue

                price = self.bybit.get_price(SYMBOL)
                if price is None:
                    time.sleep(CHECK_INTERVAL)
                    continue

                now   = datetime.now()
                today = str(now.date())

                if now.hour == 23 and now.minute == 0 and last_report != today:
                    last_report = today
                    self.send_keyboard(chat_id, self.get_daily_report())

                self._heartbeat()
                self._check_funding()

                if not self.in_trade:
                    if self.recent_high is None or price > self.recent_high:
                        self.recent_high = price

                    if self._should_enter(price):
                        ok, actual_price = self._open_level(price)
                        if ok:
                            self.first_entry_price = actual_price
                            self.in_trade          = True
                            tp, tp_pct             = self._update_tp()

                            if not self.demo_mode:
                                self.bybit.set_stop_loss_backup(
                                    SYMBOL,
                                    self._round_price(
                                        actual_price * (1 - STOP_LOSS_BACKUP_PCT / 100)
                                    )
                                )

                            drop       = round((self.recent_high - actual_price) / self.recent_high * 100, 2)
                            sl_price   = self._round_price(actual_price * (1 - STOP_LOSS_PCT / 100))
                            commission = self._calc_commission(self.entries[-1][1], actual_price)
                            bal_str    = ""
                            if self.demo_mode:
                                b       = self.bybit.get_balance_info()
                                bal_str = f"\n💳 Баланс: ${b['balance']:,.2f}\n💵 Доступно: ${b['available']:,.2f}"

                            self.send_keyboard(
                                chat_id,
                                f"🟢 ВХОД 1 {self._mode_label()}\n\n"
                                f"💲 Цена входа:  ${actual_price:,.3f}\n"
                                f"📉 Откат:       -{drop}%\n"
                                f"⚡ Плечо:       {LEVERAGE}x\n"
                                f"💵 Маржа:       ${MARGINS[0]:,.0f}\n"
                                f"📦 Куплено:     {self.entries[-1][1]:,.2f} HYPE\n"
                                f"🎯 ТП:          ${tp:,.3f} (+{tp_pct}%)\n"
                                f"🔴 СЛ:          ${sl_price:,.3f} (-{STOP_LOSS_PCT}%)\n"
                                f"💸 Комиссия:    ${commission:,.3f}"
                                f"{bal_str}"
                            )

                else:
                    drop = round(self._drop_pct(price), 2)

                    if not self.demo_mode:
                        if self.bybit.get_position_size(SYMBOL) == 0.0:
                            pnl_data   = self.bybit.get_closed_pnl(SYMBOL)
                            profit     = pnl_data["pnl"] if pnl_data else 0
                            levels     = len(self.entries)
                            is_stop    = profit < 0
                            ep         = pnl_data.get("exit_price", price) if pnl_data else price
                            commission = self._calc_commission(self.total_qty, ep)
                            self._record_trade(abs(profit), is_stop, levels, now, exit_price=ep, commission=commission)
                            self.send_keyboard(
                                chat_id,
                                f"{'❌' if is_stop else '✅'} Позиция закрыта биржей\n\n"
                                f"PnL: ${profit:,.2f} | Уровней: {levels}"
                            )
                            self._reset()
                            self.recent_high = price
                            continue

                    if self._should_stop(price):
                        levels     = len(self.entries)
                        commission = self._calc_commission(self.total_qty, price)
                        loss       = self._close_all_stop(price)
                        self._record_trade(loss, True, levels, now, exit_price=price, commission=commission)

                        bal_str = ""
                        if self.demo_mode:
                            b       = self.bybit.get_balance_info()
                            bal_str = f"\n💳 Баланс: ${b['balance']:,.2f}\n💵 Доступно: ${b['available']:,.2f}"

                        self.send_keyboard(
                            chat_id,
                            f"🔴 СТОП-ЛОСС {self._mode_label()}\n\n"
                            f"💲 Цена:        ${price:,.3f}\n"
                            f"📉 Падение:     -{drop}%\n"
                            f"💸 Убыток:      -${loss:,.2f}\n"
                            f"📊 Уровней:     {levels}\n"
                            f"❌ Стопов:      {self.stats['total_stops']}\n"
                            f"⏳ Пауза 2 мин..."
                            f"{bal_str}"
                        )
                        self._reset()
                        self.recent_high = price
                        time.sleep(120)
                        continue

                    tp_hit, tp_price, profit = self._check_tp_hit(price)
                    if tp_hit:
                        levels     = len(self.entries)
                        commission = self._calc_commission(self.total_qty, tp_price)
                        self._record_trade(profit, False, levels, now, exit_price=tp_price, commission=commission)

                        bal_str = ""
                        if self.demo_mode:
                            b       = self.bybit.get_balance_info()
                            bal_str = f"\n💳 Баланс: ${b['balance']:,.2f}\n💵 Доступно: ${b['available']:,.2f}\n📊 PnL: ${b['pnl']:+,.2f}"

                        self.send_keyboard(
                            chat_id,
                            f"✅ ТЕЙК-ПРОФИТ {self._mode_label()}\n\n"
                            f"💲 Выход:       ${tp_price:,.3f}\n"
                            f"📊 Уровней:     {levels}\n"
                            f"⚡ Плечо:       {LEVERAGE}x\n"
                            f"💵 Вложено:     ${self._total_invested():,.2f}\n"
                            f"💸 Комиссия:    ${commission:,.3f}\n"
                            f"💰 Прибыль:     +${profit:,.2f}\n\n"
                            f"✅ Сегодня: {self.stats['today_trades']} сд | +${self.stats['today_profit']:,.2f}"
                            f"{bal_str}\n\n"
                            "👀 Ищу следующий вход..."
                        )
                        self._reset()
                        self.recent_high = price
                        continue

                    averaged = False
                    while self._should_average(price) and self.current_level < len(MARGINS):
                        ok, _ = self._open_level(price)
                        if ok:
                            averaged = True
                        else:
                            break

                    if averaged:
                        tp, tp_pct = self._update_tp()
                        bal_str    = ""
                        if self.demo_mode:
                            b       = self.bybit.get_balance_info()
                            bal_str = f"\n💳 Баланс: ${b['balance']:,.2f}\n💵 Доступно: ${b['available']:,.2f}"

                        self.send_keyboard(
                            chat_id,
                            f"📉 УСРЕДНЕНИЕ {self._mode_label()} | Уровень {len(self.entries)}\n\n"
                            f"💲 Цена:        ${price:,.3f} (-{drop}%)\n"
                            f"💵 Добавлено:   ${MARGINS[self.current_level-1]:,.0f}\n"
                            f"💵 Всего:       ${self._total_invested():,.2f}\n"
                            f"📈 Средняя:     ${self.average_price:,.3f}\n"
                            f"🎯 Новый ТП:    ${tp:,.3f} (+{tp_pct}%)\n"
                            f"📦 HYPE:        {self.total_qty:,.2f}\n"
                            f"⚠️ Ур. осталось: {len(MARGINS) - self.current_level}"
                            f"{bal_str}"
                        )

                time.sleep(CHECK_INTERVAL)

            except KeyboardInterrupt:
                self.send_keyboard(self._get_chat_id(), "⏹ Бот остановлен")
                sys.exit(0)
            except Exception as e:
                logger.error(f"❌ Ошибка: {e}", exc_info=True)
                self.send_keyboard(
                    self._get_chat_id(),
                    f"⚠️ Ошибка:\n{str(e)[:150]}\n\nПродолжаю через 30с..."
                )
                time.sleep(30)


if __name__ == "__main__":
    bot = MartingaleBot()
    bot.run()
