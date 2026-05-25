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
    TAKE_PROFIT_PCT, STOP_LOSS_PCT, STOP_LOSS_BACKUP_PCT,
    COMMISSION_PCT, SMART_TP,
    CHECK_INTERVAL, HEARTBEAT_MINUTES,
    QTY_PRECISION, PRICE_PRECISION,
    DEMO_MODE, DEMO_BALANCE,
    BOT_VERSION
)
from bybit_client import BybitClient
from demo_client import DemoClient

# ── ЛОГИРОВАНИЕ ───────────────────────────────────────────────────
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

# ── ФАЙЛЫ ─────────────────────────────────────────────────────────
STATS_FILE   = "stats.json"
STATE_FILE   = "state.json"
HISTORY_FILE = "history.json"   # отдельный файл для полной истории


# ── АТОМАРНОЕ СОХРАНЕНИЕ ──────────────────────────────────────────
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


# ── ИСТОРИЯ (ГЛОБАЛЬНАЯ, НАКОПИТЕЛЬНАЯ) ───────────────────────────
def load_history() -> dict:
    """
    Полная история всех сессий.
    Каждая сессия — отдельная запись.
    """
    return _load_json(HISTORY_FILE, {
        "sessions": [],
        "all_trades": [],
        "summary": {
            "total_profit":    0.0,
            "total_trades":    0,
            "total_stops":     0,
            "total_commission": 0.0,
            "first_start":     None,
            "balance_start":   DEMO_BALANCE
        }
    })


def save_history(history: dict):
    _atomic_write(HISTORY_FILE, history)


def load_stats() -> dict:
    return _load_json(STATS_FILE, {
        "total_profit":    0.0,
        "total_trades":    0,
        "total_stops":     0,
        "today_profit":    0.0,
        "today_trades":    0,
        "today_date":      str(datetime.now().date()),
        "session_start":   datetime.now().isoformat(),
        "session_profit":  0.0,
        "session_trades":  0,
        "session_id":      1
    })


def save_stats(stats: dict):
    _atomic_write(STATS_FILE, stats)


def load_state() -> dict:
    return _load_json(STATE_FILE, {})


def save_state(state: dict):
    _atomic_write(STATE_FILE, state)


# ── ГЛАВНЫЙ КЛАСС ─────────────────────────────────────────────────
class MartingaleBot:

    def __init__(self):
        self.demo_mode      = DEMO_MODE
        self.bybit          = DemoClient(DEMO_BALANCE) if DEMO_MODE else BybitClient()
        self.running        = True
        self.stats          = load_stats()
        self.history        = load_history()
        self.start_time     = datetime.now()
        self.last_heartbeat = datetime.now()

        # Инициализируем новую сессию в истории
        self._init_session()

        self._reset()
        self._restore_state()

    # ── УПРАВЛЕНИЕ СЕССИЯМИ ───────────────────────────────────────
    def _init_session(self):
        """Создать новую сессию при каждом запуске."""
        # Если это первый запуск — зафиксируем начальный баланс
        if not self.history["summary"]["first_start"]:
            self.history["summary"]["first_start"] = datetime.now().isoformat()
            self.history["summary"]["balance_start"] = DEMO_BALANCE

        session_num = len(self.history["sessions"]) + 1
        self.current_session = {
            "session_id":   session_num,
            "version":      BOT_VERSION,
            "mode":         "DEMO" if DEMO_MODE else "REAL",
            "start":        datetime.now().isoformat(),
            "end":          None,
            "trades":       [],
            "profit":       0.0,
            "trades_count": 0,
            "stops_count":  0,
            "commission":   0.0,
            "label":        f"Сессия #{session_num} — {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        }

        self.history["sessions"].append(self.current_session)
        save_history(self.history)
        logger.info(f"📂 Создана сессия #{session_num}")

    def _record_to_history(
        self, profit: float, is_stop: bool,
        levels: int, entry_price: float,
        exit_price: float, invested: float,
        commission: float
    ):
        """Записать сделку в глобальную историю."""
        now = datetime.now()
        trade = {
            "session_id":    self.current_session["session_id"],
            "date":          now.strftime("%d.%m.%Y"),
            "time":          now.strftime("%H:%M:%S"),
            "datetime":      now.isoformat(),
            "type":          "STOP" if is_stop else "TP",
            "levels":        levels,
            "entry_price":   entry_price,
            "exit_price":    exit_price,
            "invested":      invested,
            "commission":    commission,
            "profit":        profit if not is_stop else -abs(profit),
            "mode":          "DEMO" if self.demo_mode else "REAL"
        }

        # Добавляем в общий список и в текущую сессию
        self.history["all_trades"].append(trade)
        self.current_session["trades"].append(trade)

        # Обновляем итоги сессии
        self.current_session["commission"] += commission
        if is_stop:
            self.current_session["stops_count"] += 1
            self.current_session["profit"]      -= abs(profit)
        else:
            self.current_session["trades_count"] += 1
            self.current_session["profit"]       += profit

        # Обновляем глобальное резюме
        self.history["summary"]["total_trades"] += 1
        if is_stop:
            self.history["summary"]["total_stops"]  += 1
            self.history["summary"]["total_profit"] -= abs(profit)
        else:
            self.history["summary"]["total_profit"] += profit
        self.history["summary"]["total_commission"] += commission

        save_history(self.history)

    # ── СОСТОЯНИЕ ─────────────────────────────────────────────────
    def _reset(self):
        self.in_trade          = False
        self.entries           = []
        self.current_level     = 0
        self.first_entry_price = None
        self.average_price     = None
        self.total_qty         = 0.0
        self.recent_high       = None
        save_state({})
        logger.info("🔄 Сброс — жду входа")

    def _restore_state(self):
        state = load_state()
        if not state or not state.get("in_trade"):
            return

        if not self.demo_mode:
            if self.bybit.get_position_size(SYMBOL) == 0:
                save_state({})
                return

        self.in_trade          = state.get("in_trade", False)
        self.entries           = [tuple(e) for e in state.get("entries", [])]
        self.current_level     = state.get("current_level", 0)
        self.first_entry_price = state.get("first_entry_price")
        self.average_price     = state.get("average_price")
        self.total_qty         = state.get("total_qty", 0.0)
        self.recent_high       = state.get("recent_high")

        logger.info(
            f"♻️ Восстановление | Уровень {self.current_level} | "
            f"Средняя ${self.average_price}"
        )

        if not self.demo_mode and self.in_trade and self.average_price:
            tp_pct   = self._get_tp_pct()
            tp_price = self._round_price(self.average_price * (1 + tp_pct / 100))
            self.bybit.set_take_profit(SYMBOL, tp_price)

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

    # ── HELPERS ───────────────────────────────────────────────────
    def _round_qty(self, qty):   return round(qty, QTY_PRECISION)
    def _round_price(self, p):   return round(p, PRICE_PRECISION)
    def _total_invested(self):   return sum(m for _, _, m in self.entries)
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

    # ── УСЛОВИЯ ───────────────────────────────────────────────────
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

    # ── ТОРГОВЛЯ ──────────────────────────────────────────────────
    def _open_level(self, price) -> tuple[bool, float]:
        if self.current_level >= len(MARGINS):
            return False, 0.0

        margin       = MARGINS[self.current_level]
        expected_qty = self._round_qty((margin * LEVERAGE) / price)

        result = self.bybit.place_market_buy(SYMBOL, expected_qty)
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
            f"Уровень {self.current_level}: "
            f"{actual_qty} HYPE @ ${actual_price:.3f} | "
            f"Ср: ${self.average_price:.3f}"
        )
        return True, actual_price

    def _update_tp(self) -> tuple[float | None, float]:
        tp_pct   = self._get_tp_pct()
        tp_price = self._round_price(self.average_price * (1 + tp_pct / 100))
        success  = self.bybit.set_take_profit(SYMBOL, tp_price)
        if not success:
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

    def _record_trade(self, profit, is_stop, levels, now,
                      exit_price=0.0, commission=0.0):
        """Записать сделку в stats и в глобальную историю."""
        self.stats["total_trades"] += 1
        self.stats["today_trades"] += 1
        self.stats["session_trades"] += 1

        if is_stop:
            self.stats["total_stops"]  += 1
            self.stats["total_profit"] -= profit
            self.stats["today_profit"] -= profit
            self.stats["session_profit"] -= profit
        else:
            self.stats["total_profit"] += profit
            self.stats["today_profit"] += profit
            self.stats["session_profit"] += profit

        save_stats(self.stats)

        # Записываем в глобальную историю
        self._record_to_history(
            profit       = profit,
            is_stop      = is_stop,
            levels       = levels,
            entry_price  = self.first_entry_price or 0.0,
            exit_price   = exit_price,
            invested     = self._total_invested(),
            commission   = commission
        )

    # ── DASHBOARD ─────────────────────────────────────────────────
    def _get_balance_block(self) -> str:
        if self.demo_mode:
            b = self.bybit.get_balance_info()
            return (
                f"║ 💳 Баланс:    ${b['balance']:>10,.2f}\n"
                f"║ 📊 PnL:       ${b['pnl']:>+10,.2f} ({b['pnl_pct']:+.2f}%)\n"
                f"║ 💸 Комиссии:  ${b['commission']:>10,.2f}\n"
            )
        else:
            wb = self.bybit.get_wallet_balance()
            return (
                f"║ 💳 Баланс:    ${wb['balance']:>10,.2f}\n"
                f"║ 💵 Доступно:  ${wb['available']:>10,.2f}\n"
            )

    def get_dashboard(self) -> str:
        price = self.bybit.get_price(SYMBOL)
        if not price:
            return "⚠️ Ошибка получения цены"

        self._check_new_day()
        wins    = self.stats["total_trades"] - self.stats["total_stops"]
        winrate = round(wins / self.stats["total_trades"] * 100) if self.stats["total_trades"] else 0

        if not self.in_trade:
            drop = round(
                (self.recent_high - price) / self.recent_high * 100, 2
            ) if self.recent_high else 0
            need = round(max(ENTRY_DROP_PCT - drop, 0), 2)
            return (
                f"╔═══════════════════════════════╗\n"
                f"║  🚀 HYPE BOT {self._mode_label()}\n"
                f"╠═══════════════════════════════╣\n"
                f"║ 💲 Цена:        ${price:,.3f}\n"
                f"║ 📈 Максимум:    ${self.recent_high or '—'}\n"
                f"║ 📉 Откат:       -{drop}%\n"
                f"║ 🎯 До входа:    -{need}%\n"
                f"╠═══════════════════════════════╣\n"
                f"{self._get_balance_block()}"
                f"╠═══════════════════════════════╣\n"
                f"║ 📊 СТАТИСТИКА\n"
                f"║ ✅ Сегодня:   {self.stats['today_trades']} сд | +${self.stats['today_profit']:,.2f}\n"
                f"║ 📈 Сессия:    {self.stats['session_trades']} сд | +${self.stats['session_profit']:,.2f}\n"
                f"║ 💰 Всего:     +${self.stats['total_profit']:,.2f}\n"
                f"║ ❌ Стопов:    {self.stats['total_stops']}\n"
                f"║ 🏆 Винрейт:   {winrate}%\n"
                f"╠═══════════════════════════════╣\n"
                f"║ ⚡ Плечо: {LEVERAGE}x | ⏱ {self._uptime()}\n"
                f"╚═══════════════════════════════╝"
            )
        else:
            drop     = round(self._drop_pct(price), 2)
            tp_pct   = self._get_tp_pct()
            tp_price = self._round_price(self.average_price * (1 + tp_pct / 100))
            sl_price = self._round_price(self.first_entry_price * (1 - STOP_LOSS_PCT / 100))
            pnl      = round((price - self.average_price) * self.total_qty, 2)
            emoji    = "📈" if pnl >= 0 else "📉"
            return (
                f"╔═══════════════════════════════╗\n"
                f"║  🚀 HYPE BOT {self._mode_label()}\n"
                f"╠═══════════════════════════════╣\n"
                f"║ 🟢 АКТИВНАЯ СДЕЛКА\n"
                f"║ 💲 Цена:        ${price:,.3f}\n"
                f"║ 📦 HYPE:        {self.total_qty:,.2f}\n"
                f"║ 📈 Средняя:     ${self.average_price:,.3f}\n"
                f"║ {emoji} PnL:          ${pnl:,.2f}\n"
                f"║ 📉 Падение:     -{drop}%\n"
                f"╠═══════════════════════════════╣\n"
                f"║ 💵 Вложено:     ${self._total_invested():,.2f}\n"
                f"║ 🎯 ТП:          ${tp_price:,.3f} (+{tp_pct}%)\n"
                f"║ 🔴 СЛ:          ${sl_price:,.3f} (-{STOP_LOSS_PCT}%)\n"
                f"║ 📊 Уровень:     {len(self.entries)}/{len(MARGINS)}\n"
                f"╠═══════════════════════════════╣\n"
                f"{self._get_balance_block()}"
                f"║ ⚡ Плечо: {LEVERAGE}x | ⏱ {self._uptime()}\n"
                f"╚═══════════════════════════════╝"
            )

    def get_history(self) -> str:
        """История сделок с разделением по сессиям."""
        h = self.history
        if not h["sessions"]:
            return "📋 История пуста"

        text  = f"📋 ПОЛНАЯ ИСТОРИЯ {self._mode_label()}\n"
        text += f"{'═'*35}\n\n"

        # Сводка по всем сессиям
        s = h["summary"]
        text += (
            f"📊 ОБЩИЙ ИТОГ:\n"
            f"💰 Прибыль: +${s['total_profit']:,.2f}\n"
            f"✅ Сделок: {s['total_trades']} | ❌ Стопов: {s['total_stops']}\n"
            f"💸 Комиссий: ${s['total_commission']:,.2f}\n\n"
        )

        # По каждой сессии
        for sess in reversed(h["sessions"]):
            sid    = sess["session_id"]
            label  = sess["label"]
            profit = sess["profit"]
            count  = sess["trades_count"] + sess["stops_count"]
            emoji  = "✅" if profit >= 0 else "❌"

            text += f"{'─'*35}\n"
            text += f"🗂 {label}\n"
            text += f"{emoji} Прибыль: ${profit:+,.2f} | Сделок: {count}\n"

            # Последние 5 сделок сессии
            for t in sess["trades"][-5:][::-1]:
                t_emoji = "✅" if t["profit"] >= 0 else "❌"
                text += (
                    f"  {t_emoji} {t['date']} {t['time']}\n"
                    f"     Ур:{t['levels']} | "
                    f"Вход:${t['entry_price']:.3f} → "
                    f"Выход:${t['exit_price']:.3f} | "
                    f"+${t['profit']:,.2f}\n"
                )

            if len(sess["trades"]) > 5:
                text += f"  ... ещё {len(sess['trades'])-5} сделок\n"

        return text

    def get_daily_report(self) -> str:
        self._check_new_day()
        wins    = self.stats["total_trades"] - self.stats["total_stops"]
        winrate = round(wins / self.stats["total_trades"] * 100) if self.stats["total_trades"] else 0

        bal = ""
        if self.demo_mode:
            b   = self.bybit.get_balance_info()
            bal = (
                f"💳 Баланс: ${b['balance']:,.2f}\n"
                f"💸 Комиссий всего: ${b['commission']:,.2f}\n"
                f"📊 PnL: ${b['pnl']:+,.2f} ({b['pnl_pct']:+.2f}%)\n\n"
            )

        s = self.history["summary"]
        return (
            f"📊 ИТОГИ ДНЯ {self._mode_label()}\n"
            f"{datetime.now().strftime('%d.%m.%Y')}\n\n"
            f"{bal}"
            f"✅ Сегодня: {self.stats['today_trades']} сделок\n"
            f"💰 Сегодня: +${self.stats['today_profit']:,.2f}\n\n"
            f"📈 ЗА ВСЁ ВРЕМЯ:\n"
            f"💰 Прибыль: +${s['total_profit']:,.2f}\n"
            f"✅ Сделок: {s['total_trades']} | ❌ Стопов: {s['total_stops']}\n"
            f"💸 Комиссий: ${s['total_commission']:,.2f}\n"
            f"🏆 Винрейт: {winrate}%\n"
            f"⚡ Плечо: {LEVERAGE}x\n"
            f"⏱ Аптайм: {self._uptime()}"
        )

    # ── TELEGRAM ──────────────────────────────────────────────────
    def send_keyboard(self, chat_id: str, text: str):
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
                [{"text": "⏹ Стоп бот",  "callback_data": "stop"}]
            ]
        }
        try:
            from config import TELEGRAM_TOKEN
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": text, "reply_markup": keyboard},
                timeout=10
            )
        except Exception as e:
            logger.error(f"send_keyboard: {e}")

    def _answer_cb(self, cb_id: str):
        try:
            from config import TELEGRAM_TOKEN
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery",
                json={"callback_query_id": cb_id},
                timeout=5
            )
        except:
            pass

    def poll_telegram(self):
        from config import TELEGRAM_TOKEN
        try:
            requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook",
                params={"drop_pending_updates": True},
                timeout=10
            )
        except:
            pass

        url, offset = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates", 0
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
                        self._handle_cmd("/" + cb["data"],
                                         str(cb["message"]["chat"]["id"]))
            except Exception as e:
                logger.error(f"poll_telegram: {e}")
                time.sleep(5)

    def _handle_cmd(self, cmd: str, chat_id: str):
        if cmd in ("/start", "/status"):
            self.send_keyboard(chat_id, self.get_dashboard())
        elif cmd == "/price":
            p = self.bybit.get_price(SYMBOL)
            self.send_keyboard(chat_id, f"💲 HYPE: ${p:,.3f}")
        elif cmd == "/history":
            self.send_keyboard(chat_id, self.get_history())
        elif cmd == "/report":
            self.send_keyboard(chat_id, self.get_daily_report())
        elif cmd == "/stop":
            self.running = False
            self.send_keyboard(chat_id, "⏹ Бот остановлен")

    # ── HEARTBEAT ─────────────────────────────────────────────────
    def _heartbeat(self):
        diff = (datetime.now() - self.last_heartbeat).total_seconds() / 60
        if diff < HEARTBEAT_MINUTES:
            return
        self.last_heartbeat = datetime.now()
        price  = self.bybit.get_price(SYMBOL)
        status = "🟢 В позиции" if self.in_trade else "👀 Жду входа"
        self.send_keyboard(
            self._get_chat_id(),
            f"💓 БОТ АКТИВЕН\n\n"
            f"⚡ Статус: {status}\n"
            f"💲 HYPE: ${price:,.3f}\n"
            f"⏱ Аптайм: {self._uptime()}\n"
            f"💰 Всего прибыли: +${self.stats['total_profit']:,.2f}\n"
            f"✅ Сделок: {self.stats['total_trades']} | ❌ Стопов: {self.stats['total_stops']}"
        )

    # ── ГЛАВНЫЙ ЦИКЛ ──────────────────────────────────────────────
    def run(self):
        logger.info(
            f"🚀 ЗАПУСК v{BOT_VERSION} | "
            f"{'ДЕМО' if self.demo_mode else 'РЕАЛ'} | "
            f"Плечо {LEVERAGE}x | CHECK: {CHECK_INTERVAL}с"
        )

        threading.Thread(target=self.poll_telegram, daemon=True).start()
        chat_id = self._get_chat_id()

        setup     = self.bybit.auto_setup(SYMBOL, LEVERAGE)
        demo_note = f"\n\n🎮 ДЕМО | Баланс: ${DEMO_BALANCE:,.2f}" if self.demo_mode else ""
        restored  = "\n♻️ Состояние восстановлено!" if self.in_trade else ""

        # Сессия
        sess_num = self.current_session["session_id"]
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
                price = self.bybit.get_price(SYMBOL)
                if price is None:
                    time.sleep(CHECK_INTERVAL)
                    continue

                now   = datetime.now()
                today = str(now.date())

                # Ежедневный отчёт в 23:00
                if now.hour == 23 and now.minute == 0 and last_report != today:
                    last_report = today
                    self.send_keyboard(chat_id, self.get_daily_report())

                self._heartbeat()

                # ── НЕТ ПОЗИЦИИ ──────────────────────────────────
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
                                sl_backup = self._round_price(
                                    actual_price * (1 - STOP_LOSS_BACKUP_PCT / 100)
                                )
                                self.bybit.set_stop_loss_backup(SYMBOL, sl_backup)

                            drop       = round((self.recent_high - actual_price)
                                               / self.recent_high * 100, 2)
                            sl_price   = self._round_price(
                                actual_price * (1 - STOP_LOSS_PCT / 100)
                            )
                            commission = self._calc_commission(
                                self.entries[-1][1], actual_price
                            )
                            bal_str = ""
                            if self.demo_mode:
                                b = self.bybit.get_balance_info()
                                bal_str = f"\n💳 Баланс: ${b['balance']:,.2f}"

                            self.send_keyboard(
                                chat_id,
                                f"🟢 ВХОД 1 {self._mode_label()}\n\n"
                                f"💲 Цена входа:   ${actual_price:,.3f}\n"
                                f"📉 Откат:        -{drop}%\n"
                                f"⚡ Плечо:        {LEVERAGE}x\n"
                                f"💵 Маржа:        ${MARGINS[0]:,.0f}\n"
                                f"📦 Куплено:      {self.entries[-1][1]:,.2f} HYPE\n"
                                f"🎯 ТП:           ${tp:,.3f} (+{tp_pct}%)\n"
                                f"🔴 СЛ:           ${sl_price:,.3f} (-{STOP_LOSS_PCT}%)\n"
                                f"💸 Комиссия:     ${commission:,.3f}"
                                f"{bal_str}"
                            )

                # ── ЕСТЬ ПОЗИЦИЯ ──────────────────────────────────
                else:
                    drop = round(self._drop_pct(price), 2)

                    # 1. Защита зависшей позиции (только реал)
                    if not self.demo_mode:
                        if self.bybit.get_position_size(SYMBOL) == 0.0:
                            pnl_data = self.bybit.get_closed_pnl(SYMBOL)
                            profit   = pnl_data["pnl"] if pnl_data else 0
                            levels   = len(self.entries)
                            is_stop  = profit < 0
                            commission = abs(profit) * COMMISSION_PCT / 100

                            self._record_trade(
                                abs(profit), is_stop, levels, now,
                                exit_price = pnl_data.get("exit_price", price) if pnl_data else price,
                                commission = commission
                            )
                            self.send_keyboard(
                                chat_id,
                                f"{'❌' if is_stop else '✅'} Позиция закрыта биржей\n\n"
                                f"PnL: ${profit:,.2f} | Уровней: {levels}"
                            )
                            self._reset()
                            self.recent_high = price
                            continue

                    # 2. Программный стоп-лосс
                    if self._should_stop(price):
                        levels     = len(self.entries)
                        commission = self._calc_commission(self.total_qty, price)
                        loss       = self._close_all_stop(price)

                        self._record_trade(
                            loss, True, levels, now,
                            exit_price = price,
                            commission = commission
                        )

                        bal_str = ""
                        if self.demo_mode:
                            b = self.bybit.get_balance_info()
                            bal_str = f"\n💳 Баланс: ${b['balance']:,.2f}"

                        self.send_keyboard(
                            chat_id,
                            f"🔴 СТОП-ЛОСС {self._mode_label()}\n\n"
                            f"💲 Цена:         ${price:,.3f}\n"
                            f"📉 Падение:      -{drop}%\n"
                            f"💸 Убыток:       -${loss:,.2f}\n"
                            f"📊 Уровней:      {levels}\n"
                            f"❌ Стопов всего: {self.stats['total_stops']}\n"
                            f"⏳ Пауза 2 мин..."
                            f"{bal_str}"
                        )
                        self._reset()
                        self.recent_high = price
                        time.sleep(120)
                        continue

                    # 3. Тейк-профит
                    tp_hit, tp_price, profit = self._check_tp_hit(price)
                    if tp_hit:
                        levels     = len(self.entries)
                        commission = self._calc_commission(self.total_qty, tp_price)

                        self._record_trade(
                            profit, False, levels, now,
                            exit_price = tp_price,
                            commission = commission
                        )

                        bal_str = ""
                        if self.demo_mode:
                            b = self.bybit.get_balance_info()
                            bal_str = f"\n💳 Баланс: ${b['balance']:,.2f}"

                        self.send_keyboard(
                            chat_id,
                            f"✅ ТЕЙК-ПРОФИТ {self._mode_label()}\n\n"
                            f"💲 Цена выхода:  ${tp_price:,.3f}\n"
                            f"📊 Уровней:      {levels}\n"
                            f"⚡ Плечо:        {LEVERAGE}x\n"
                            f"💵 Вложено:      ${self._total_invested():,.2f}\n"
                            f"💸 Комиссия:     ${commission:,.3f}\n"
                            f"💰 Прибыль:      +${profit:,.2f}\n\n"
                            f"✅ Сегодня: {self.stats['today_trades']} сд | +${self.stats['today_profit']:,.2f}\n"
                            f"💰 Всего: +${self.stats['total_profit']:,.2f}"
                            f"{bal_str}\n\n"
                            "👀 Ищу следующий вход..."
                        )
                        self._reset()
                        self.recent_high = price
                        continue

                    # 4. Усреднение
                    averaged = False
                    while (self._should_average(price)
                           and self.current_level < len(MARGINS)):
                        ok, _ = self._open_level(price)
                        if ok:
                            averaged = True
                        else:
                            break

                    if averaged:
                        tp, tp_pct = self._update_tp()
                        commission = self._calc_commission(self.total_qty, price)
                        bal_str    = ""
                        if self.demo_mode:
                            b = self.bybit.get_balance_info()
                            bal_str = f"\n💳 Баланс: ${b['balance']:,.2f}"

                        self.send_keyboard(
                            chat_id,
                            f"📉 УСРЕДНЕНИЕ {self._mode_label()} "
                            f"| Уровень {len(self.entries)}\n\n"
                            f"💲 Цена:         ${price:,.3f} (-{drop}%)\n"
                            f"💵 Добавлено:    ${MARGINS[self.current_level-1]:,.0f}\n"
                            f"💵 Всего:        ${self._total_invested():,.2f}\n"
                            f"📈 Средняя:      ${self.average_price:,.3f}\n"
                            f"🎯 Новый ТП:     ${tp:,.3f} (+{tp_pct}%)\n"
                            f"📦 HYPE:         {self.total_qty:,.2f}\n"
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
