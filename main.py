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
    DEMO_MODE, DEMO_BALANCE
)
from bybit_client import BybitClient
from demo_client import DemoClient
from notifier import TelegramNotifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)

STATS_FILE = "stats.json"
STATE_FILE = "state.json"


# ── АТОМАРНОЕ СОХРАНЕНИЕ ──────────────────────────────────────────
def _atomic_write(filepath: str, data: dict):
    """Записывает через временный файл — защита от обрыва"""
    tmp = filepath + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, filepath)


def load_stats() -> dict:
    try:
        if os.path.exists(STATS_FILE):
            with open(STATS_FILE) as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"load_stats: {e}")
    return {
        "total_profit": 0.0,
        "total_trades": 0,
        "total_stops":  0,
        "today_profit": 0.0,
        "today_trades": 0,
        "today_date":   str(datetime.now().date()),
        "history":      []
    }


def save_stats(stats: dict):
    _atomic_write(STATS_FILE, stats)


def load_state() -> dict:
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE) as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"load_state: {e}")
    return {}


def save_state(state: dict):
    _atomic_write(STATE_FILE, state)


class MartingaleBot:

    def __init__(self):
        self.demo_mode      = DEMO_MODE
        self.bybit          = DemoClient(DEMO_BALANCE) if DEMO_MODE else BybitClient()
        self.notifier       = TelegramNotifier()
        self.running        = True
        self.stats          = load_stats()
        self.start_time     = datetime.now()
        self.last_heartbeat = datetime.now()
        self._reset()
        self._restore_state()

    # ── СОСТОЯНИЕ ─────────────────────────────────────────────────
    def _reset(self):
        self.in_trade          = False
        self.entries           = []   # [(actual_price, qty, margin), ...]
        self.current_level     = 0
        self.first_entry_price = None
        self.average_price     = None
        self.total_qty         = 0.0
        self.recent_high       = None
        save_state({})
        logger.info("Сброс — жду входа")

    def _restore_state(self):
        """Восстановление после перезапуска с проверкой биржи"""
        state = load_state()
        if not state or not state.get("in_trade"):
            return

        if not self.demo_mode:
            real_size = self.bybit.get_position_size(SYMBOL)
            if real_size == 0:
                logger.warning("Позиции на бирже нет — сбрасываем")
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
            f"♻️ Состояние восстановлено | "
            f"Уровень {self.current_level} | "
            f"Средняя ${self.average_price}"
        )

        # Переставить TP после рестарта
        if not self.demo_mode and self.in_trade and self.average_price:
            tp_pct   = self._get_tp_pct()
            tp_price = self._round_price(
                self.average_price * (1 + tp_pct / 100)
            )
            self.bybit.set_take_profit(SYMBOL, tp_price)
            logger.info(f"♻️ TP переставлен @ ${tp_price}")

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
    def _round_qty(self, qty: float) -> float:
        return round(qty, QTY_PRECISION)

    def _round_price(self, price: float) -> float:
        return round(price, PRICE_PRECISION)

    def _calc_average(self) -> float:
        cost = sum(p * q for p, q, _ in self.entries)
        qty  = sum(q for _, q, _ in self.entries)
        return cost / qty if qty else 0.0

    def _total_invested(self) -> float:
        return sum(m for _, _, m in self.entries)

    def _drop_pct(self, price: float) -> float:
        if not self.first_entry_price:
            return 0.0
        return (self.first_entry_price - price) / self.first_entry_price * 100

    def _get_tp_pct(self) -> float:
        idx = min(max(self.current_level - 1, 0), len(SMART_TP) - 1)
        return SMART_TP[idx]

    def _calc_commission(self, qty: float, price: float) -> float:
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

    def _uptime(self) -> str:
        d   = datetime.now() - self.start_time
        tot = int(d.total_seconds())
        h   = tot // 3600
        m   = (tot % 3600) // 60
        return f"{h}ч {m}м"

    def _mode_label(self) -> str:
        return "🎮 ДЕМО" if self.demo_mode else "💰 РЕАЛ"

    def _get_chat_id(self) -> str:
        from config import TELEGRAM_CHAT_ID
        return TELEGRAM_CHAT_ID

    # ── УСЛОВИЯ ВХОДА ─────────────────────────────────────────────
    def _should_enter(self, price: float) -> bool:
        if self.recent_high is None:
            return False
        return (
            (self.recent_high - price) / self.recent_high * 100
            >= ENTRY_DROP_PCT
        )

    def _should_average(self, price: float) -> bool:
        if not self.in_trade or self.current_level >= len(MARGINS):
            return False
        return self._drop_pct(price) >= self.current_level * AVERAGING_STEP_PCT

    def _should_stop(self, price: float) -> bool:
        return self.in_trade and self._drop_pct(price) >= STOP_LOSS_PCT

    # ── ТОРГОВЫЕ ДЕЙСТВИЯ ─────────────────────────────────────────
    def _open_level(self, price: float) -> tuple[bool, float]:
        """Открыть уровень. Возвращает (успех, реальная цена входа)"""
        if self.current_level >= len(MARGINS):
            logger.warning("Все уровни исчерпаны!")
            return False, 0.0

        margin       = MARGINS[self.current_level]
        expected_qty = self._round_qty((margin * LEVERAGE) / price)

        result = self.bybit.place_market_buy(SYMBOL, expected_qty)
        if not result:
            logger.error(f"Ордер отклонён на уровне {self.current_level + 1}")
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
        """Установить/обновить тейк-профит. Возвращает (tp_price, tp_pct)"""
        tp_pct   = self._get_tp_pct()
        tp_price = self._round_price(self.average_price * (1 + tp_pct / 100))

        success = self.bybit.set_take_profit(SYMBOL, tp_price)
        if not success:
            logger.error("Не удалось установить TP!")
            return None, tp_pct

        self._save_state()
        return tp_price, tp_pct

    def _check_tp_hit(
        self, current_price: float
    ) -> tuple[bool, float, float]:
        """
        Проверяет сработал ли TP.
        Возвращает: (сработал, цена_выхода, прибыль)
        """
        if not self.in_trade:
            return False, 0.0, 0.0

        if self.demo_mode:
            hit, tp_price = self.bybit.check_tp_triggered(current_price)
            if hit:
                pnl_data = self.bybit.get_closed_pnl(SYMBOL)
                profit   = pnl_data["pnl"] if pnl_data else round(
                    self.total_qty * (tp_price - self.average_price)
                    - self._calc_commission(self.total_qty, tp_price), 2
                )
                return True, tp_price, profit
            return False, 0.0, 0.0
        else:
            pos_size = self.bybit.get_position_size(SYMBOL)
            if pos_size == 0.0:
                pnl_data = self.bybit.get_closed_pnl(SYMBOL)
                if pnl_data:
                    return True, pnl_data["exit_price"], pnl_data["pnl"]
                # Fallback
                tp_pct  = self._get_tp_pct()
                profit  = round(
                    self._total_invested() * LEVERAGE * tp_pct / 100, 2
                )
                return True, current_price, profit
            return False, 0.0, 0.0

    def _close_all_stop(self, stop_price: float) -> float:
        """
        Экстренное закрытие. Возвращает величину убытка (положительное число).
        """
        self.bybit.cancel_all_orders(SYMBOL)
        self.bybit.market_close_all(SYMBOL, self.total_qty)

        # Пробуем получить реальный P&L
        time.sleep(0.5)
        pnl_data = self.bybit.get_closed_pnl(SYMBOL)
        if pnl_data:
            return round(abs(pnl_data["pnl"]), 2)

        # Fallback — считаем из цен
        commission = self._calc_commission(self.total_qty, stop_price)
        loss = (
            self.total_qty * (self.average_price - stop_price) + commission
        )
        return round(max(loss, 0), 2)

    # ── DASHBOARD ─────────────────────────────────────────────────
    def _get_balance_line(self) -> str:
        if self.demo_mode:
            b = self.bybit.get_balance_info()
            return (
                f"║ 💳 Баланс:    ${b['balance']}\n"
                f"║ 📊 PnL:       ${b['pnl']} ({b['pnl_pct']}%)\n"
                f"║ 💸 Комиссии:  ${b['commission']}\n"
            )
        else:
            wb = self.bybit.get_wallet_balance()
            return (
                f"║ 💳 Баланс:    ${wb['balance']}\n"
                f"║ 💵 Доступно:  ${wb['available']}\n"
            )

    def get_dashboard(self) -> str:
        price = self.bybit.get_price(SYMBOL)
        if not price:
            return "⚠️ Нет данных о цене"
        self._check_new_day()
        winrate = 0
        if self.stats["total_trades"] > 0:
            wins    = self.stats["total_trades"] - self.stats["total_stops"]
            winrate = round(wins / self.stats["total_trades"] * 100)

        if not self.in_trade:
            drop = (
                round((self.recent_high - price) / self.recent_high * 100, 2)
                if self.recent_high else 0
            )
            need = round(max(ENTRY_DROP_PCT - drop, 0), 2)
            return (
                f"╔══════════════════════════╗\n"
                f"║  🚀 HYPE BOT {self._mode_label()}\n"
                f"╠══════════════════════════╣\n"
                f"║ 💲 Цена:      ${price}\n"
                f"║ 📈 Макс:      ${self.recent_high or '—'}\n"
                f"║ 📉 Откат:     -{drop}%\n"
                f"║ 🎯 До входа:  -{need}%\n"
                f"╠══════════════════════════╣\n"
                f"{self._get_balance_line()}"
                f"║ 📊 СТАТИСТИКА\n"
                f"║ ✅ Сегодня: {self.stats['today_trades']} сделок\n"
                f"║ 💰 Сегодня: +${round(self.stats['today_profit'], 2)}\n"
                f"║ 💰 Всего:   +${round(self.stats['total_profit'], 2)}\n"
                f"║ ❌ Стопов:   {self.stats['total_stops']}\n"
                f"║ 🏆 Винрейт:  {winrate}%\n"
                f"╠══════════════════════════╣\n"
                f"║ ⚡ Плечо: {LEVERAGE}x | {self._mode_label()}\n"
                f"║ ⏱ Аптайм: {self._uptime()}\n"
                f"╚══════════════════════════╝"
            )
        else:
            drop     = round(self._drop_pct(price), 2)
            tp_pct   = self._get_tp_pct()
            tp_price = self._round_price(
                self.average_price * (1 + tp_pct / 100)
            )
            sl_price = self._round_price(
                self.first_entry_price * (1 - STOP_LOSS_PCT / 100)
            )
            pnl       = round(
                (price - self.average_price) * self.total_qty, 2
            )
            pnl_emoji = "📈" if pnl >= 0 else "📉"
            return (
                f"╔══════════════════════════╗\n"
                f"║  🚀 HYPE BOT {self._mode_label()}\n"
                f"╠══════════════════════════╣\n"
                f"║ 🟢 АКТИВНАЯ СДЕЛКА\n"
                f"║ 💲 Цена:    ${price}\n"
                f"║ 📦 HYPE:    {self.total_qty}\n"
                f"║ 📈 Средняя: ${self.average_price}\n"
                f"║ {pnl_emoji} PnL:     ${pnl}\n"
                f"║ 📉 Падение: -{drop}%\n"
                f"╠══════════════════════════╣\n"
                f"║ 💵 Вложено: ${self._total_invested()}\n"
                f"║ 🎯 ТП:      ${tp_price} (+{tp_pct}%)\n"
                f"║ 🔴 СЛ:      ${sl_price} (-{STOP_LOSS_PCT}%)\n"
                f"║ 📊 Уровень: {len(self.entries)}/{len(MARGINS)}\n"
                f"╠══════════════════════════╣\n"
                f"{self._get_balance_line()}"
                f"║ ⚡ Плечо: {LEVERAGE}x\n"
                f"║ ⏱ Аптайм: {self._uptime()}\n"
                f"╚══════════════════════════╝"
            )

    def get_history(self) -> str:
        h = self.stats.get("history", [])
        if not h:
            return "📋 История пуста"
        text = f"📋 ПОСЛЕДНИЕ СДЕЛКИ {self._mode_label()}\n\n"
        for t in h[-5:][::-1]:
            e = "✅" if t["profit"] > 0 else "❌"
            text += f"{e} {t['date']}\n   Ур: {t['levels']} | ${t['profit']}\n\n"
        text += f"💰 Всего: +${round(self.stats['total_profit'], 2)}"
        return text

    def get_daily_report(self) -> str:
        self._check_new_day()
        winrate = 0
        if self.stats["total_trades"] > 0:
            wins    = self.stats["total_trades"] - self.stats["total_stops"]
            winrate = round(wins / self.stats["total_trades"] * 100)
        bal = ""
        if self.demo_mode:
            b   = self.bybit.get_balance_info()
            bal = f"💳 Баланс: ${b['balance']} | Комиссий: ${b['commission']}\n"
        return (
            f"📊 ИТОГИ ДНЯ {self._mode_label()}\n"
            f"{datetime.now().strftime('%d.%m.%Y')}\n\n"
            f"{bal}"
            f"✅ Сделок: {self.stats['today_trades']}\n"
            f"💰 Сегодня: +${round(self.stats['today_profit'], 2)}\n"
            f"💰 Всего: +${round(self.stats['total_profit'], 2)}\n"
            f"❌ Стопов: {self.stats['total_stops']}\n"
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
                [{"text": "⏹ Стоп бот", "callback_data": "stop"}]
            ]
        }
        try:
            from config import TELEGRAM_TOKEN
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={
                    "chat_id":      chat_id,
                    "text":         text,
                    "reply_markup": keyboard
                },
                timeout=10
            )
        except Exception as e:
            logger.error(f"send_keyboard: {e}")

    def _answer_cb(self, cb_id: str):
        try:
            from config import TELEGRAM_TOKEN
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery",
                json={"callback_query_id": cb_id}, timeout=5
            )
        except:
            pass

    def poll_telegram(self):
        from config import TELEGRAM_TOKEN
        url, offset = (
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates", 0
        )
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
            self.send_keyboard(chat_id, self.get_dashboard())
        elif cmd == "/price":
            p = self.bybit.get_price(SYMBOL)
            self.send_keyboard(chat_id, f"💲 HYPE: ${p}")
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
        status = "🟢 В сделке" if self.in_trade else "👀 Жду входа"
        self.send_keyboard(
            self._get_chat_id(),
            f"💓 БОТ АКТИВЕН\n\n"
            f"⚡ Статус: {status}\n"
            f"💲 HYPE: ${price}\n"
            f"⏱ Аптайм: {self._uptime()}\n"
            f"💰 Прибыль всего: +${round(self.stats['total_profit'], 2)}"
        )

    # ── ЗАПИСЬ СДЕЛКИ ─────────────────────────────────────────────
    def _record_trade(
        self, profit: float, is_stop: bool,
        levels: int, now: datetime
    ):
        self.stats["total_trades"] += 1
        self.stats["today_trades"] += 1
        if is_stop:
            self.stats["total_stops"]  += 1
            self.stats["total_profit"] -= profit
            self.stats["today_profit"] -= profit
        else:
            self.stats["total_profit"] += profit
            self.stats["today_profit"] += profit
        self.stats["history"].append({
            "date":   now.strftime("%d.%m %H:%M"),
            "levels": levels,
            "profit": profit if not is_stop else -profit
        })
        save_stats(self.stats)

    # ── ГЛАВНЫЙ ЦИКЛ ──────────────────────────────────────────────
    def run(self):
        logger.info(
            f"Бот запущен | "
            f"{'ДЕМО' if self.demo_mode else 'РЕАЛ'} | "
            f"Плечо {LEVERAGE}x"
        )

        threading.Thread(target=self.poll_telegram, daemon=True).start()

        chat_id = self._get_chat_id()

        # Автонастройка Bybit
        setup = self.bybit.auto_setup(SYMBOL, LEVERAGE)
        setup_str = (
            f"⚙️ Настройка:\n"
            f"  {setup.get('mode','?')}\n"
            f"  {setup.get('leverage','?')}\n"
            f"  min_qty={setup.get('min_qty','?')}\n"
        )

        demo_note = (
            f"\n\n🎮 ДЕМО РЕЖИМ\n💳 Баланс: ${DEMO_BALANCE}"
            if self.demo_mode else ""
        )
        restored = "\n♻️ Состояние восстановлено!" if self.in_trade else ""

        self.send_keyboard(
            chat_id,
            f"🚀 HYPE BOT PRO запущен!\n\n"
            f"{setup_str}\n"
            f"📊 {SYMBOL} | ⚡ {LEVERAGE}x\n"
            f"📈 Уровней: {len(MARGINS)}\n"
            f"🎯 ТП: {SMART_TP}\n"
            f"📉 Откат входа: -{ENTRY_DROP_PCT}%\n"
            f"🔴 Стоп: -{STOP_LOSS_PCT}% | Backup: -{STOP_LOSS_BACKUP_PCT}%"
            f"{demo_note}"
            f"{restored}\n\n"
            "👀 Слежу за ценой HYPE..."
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

                # Heartbeat
                self._heartbeat()

                # ── НЕТ СДЕЛКИ ────────────────────────────────────
                if not self.in_trade:
                    if self.recent_high is None or price > self.recent_high:
                        self.recent_high = price

                    if self._should_enter(price):
                        ok, actual_price = self._open_level(price)
                        if ok:
                            self.first_entry_price = actual_price
                            self.in_trade = True
                            tp, tp_pct    = self._update_tp()

                            # Backup SL на Bybit (страховка)
                            if not self.demo_mode:
                                sl_backup = self._round_price(
                                    actual_price
                                    * (1 - STOP_LOSS_BACKUP_PCT / 100)
                                )
                                self.bybit.set_stop_loss_backup(
                                    SYMBOL, sl_backup
                                )

                            drop = round(
                                (self.recent_high - actual_price)
                                / self.recent_high * 100, 2
                            )
                            sl_price   = self._round_price(
                                actual_price * (1 - STOP_LOSS_PCT / 100)
                            )
                            commission = self._calc_commission(
                                self.entries[-1][1], actual_price
                            )
                            bal_str = ""
                            if self.demo_mode:
                                b = self.bybit.get_balance_info()
                                bal_str = f"\n💳 Баланс: ${b['balance']}"

                            self.send_keyboard(
                                chat_id,
                                f"🟢 ВХОД 1 {self._mode_label()}\n\n"
                                f"💲 Цена входа: ${actual_price}\n"
                                f"📉 Откат: -{drop}%\n"
                                f"⚡ Плечо: {LEVERAGE}x\n"
                                f"💵 Маржа: ${MARGINS[0]}\n"
                                f"📦 Куплено: {self.entries[-1][1]} HYPE\n"
                                f"🎯 ТП: ${tp} (+{tp_pct}%)\n"
                                f"🔴 СЛ: ${sl_price} (-{STOP_LOSS_PCT}%)\n"
                                f"💸 Комиссия: ${commission}"
                                f"{bal_str}"
                            )

                # ── ЕСТЬ СДЕЛКА ───────────────────────────────────
                else:
                    drop = round(self._drop_pct(price), 2)

                    # 1. Защита от зависшей позиции (только реал)
                    if not self.demo_mode:
                        pos_size = self.bybit.get_position_size(SYMBOL)
                        if pos_size == 0.0:
                            pnl_data = self.bybit.get_closed_pnl(SYMBOL)
                            profit   = pnl_data["pnl"] if pnl_data else 0
                            levels   = len(self.entries)
                            is_stop  = profit < 0

                            if is_stop:
                                self._record_trade(abs(profit), True, levels, now)
                                self.send_keyboard(
                                    chat_id,
                                    f"⚠️ Позиция закрыта внешним образом\n"
                                    f"(SL Bybit или ликвидация)\n\n"
                                    f"💸 PnL: ${profit:.2f}\n"
                                    f"📊 Уровней было: {levels}"
                                )
                            else:
                                self._record_trade(profit, False, levels, now)
                                self.send_keyboard(
                                    chat_id,
                                    f"✅ TP сработал на Bybit\n\n"
                                    f"💰 Прибыль: +${profit:.2f}\n"
                                    f"📊 Уровней: {levels}"
                                )

                            self._reset()
                            self.recent_high = price
                            continue

                    # 2. Стоп-лосс (программный)
                    if self._should_stop(price):
                        levels = len(self.entries)
                        loss   = self._close_all_stop(price)
                        self._record_trade(loss, True, levels, now)

                        bal_str = ""
                        if self.demo_mode:
                            b = self.bybit.get_balance_info()
                            bal_str = f"\n💳 Баланс: ${b['balance']}"

                        self.send_keyboard(
                            chat_id,
                            f"🔴 СТОП-ЛОСС {self._mode_label()}\n\n"
                            f"💲 Цена: ${price}\n"
                            f"📉 Падение: -{drop}%\n"
                            f"💸 Потеря: ~${loss}\n"
                            f"📊 Уровней было: {levels}\n"
                            f"❌ Стопов всего: {self.stats['total_stops']}\n"
                            f"⏳ Пауза 2 минуты..."
                            f"{bal_str}"
                        )
                        self._reset()
                        self.recent_high = price
                        time.sleep(120)
                        continue

                    # 3. Тейк-профит
                    tp_hit, tp_price, profit = self._check_tp_hit(price)
                    if tp_hit:
                        levels = len(self.entries)
                        self._record_trade(profit, False, levels, now)

                        bal_str = ""
                        if self.demo_mode:
                            b = self.bybit.get_balance_info()
                            bal_str = f"\n💳 Баланс: ${b['balance']}"

                        self.send_keyboard(
                            chat_id,
                            f"✅ ТЕЙК-ПРОФИТ {self._mode_label()}\n\n"
                            f"💲 Цена выхода: ${tp_price}\n"
                            f"📊 Уровней: {levels}\n"
                            f"⚡ Плечо: {LEVERAGE}x\n"
                            f"💵 Вложено: ${self._total_invested()}\n"
                            f"💰 Прибыль: +${profit}\n\n"
                            f"✅ Сегодня: {self.stats['today_trades']} сделок\n"
                            f"💰 Сегодня: +${round(self.stats['today_profit'], 2)}\n"
                            f"💰 Всего: +${round(self.stats['total_profit'], 2)}"
                            f"{bal_str}\n\n"
                            "👀 Ищу следующий вход..."
                        )
                        self._reset()
                        self.recent_high = price
                        continue

                    # 4. Усреднение
                    averaged = False
                    while (
                        self._should_average(price)
                        and self.current_level < len(MARGINS)
                    ):
                        ok, _ = self._open_level(price)
                        if ok:
                            averaged = True
                        else:
                            break  # Попробуем на следующем тике

                    if averaged:
                        tp, tp_pct = self._update_tp()
                        commission = self._calc_commission(
                            self.total_qty, price
                        )
                        bal_str = ""
                        if self.demo_mode:
                            b = self.bybit.get_balance_info()
                            bal_str = f"\n💳 Баланс: ${b['balance']}"

                        self.send_keyboard(
                            chat_id,
                            f"📉 УСРЕДНЕНИЕ {self._mode_label()} "
                            f"— Уровень {len(self.entries)}\n\n"
                            f"💲 Цена: ${price} (-{drop}%)\n"
                            f"💵 Добавлено: ${MARGINS[self.current_level-1]}\n"
                            f"📊 Вложено: ${self._total_invested()}\n"
                            f"📈 Средняя: ${self.average_price}\n"
                            f"🎯 Новый ТП: ${tp} (+{tp_pct}%)\n"
                            f"📦 HYPE: {self.total_qty}\n"
                            f"⚠️ Осталось уровней: "
                            f"{len(MARGINS) - self.current_level}"
                            f"{bal_str}"
                        )

                time.sleep(CHECK_INTERVAL)

            except KeyboardInterrupt:
                self.send_keyboard(self._get_chat_id(), "⏹ Бот остановлен")
                sys.exit(0)
            except Exception as e:
                logger.error(f"Ошибка: {e}", exc_info=True)
                self.send_keyboard(
                    self._get_chat_id(),
                    f"⚠️ Ошибка:\n{e}\n\nПродолжаю через 30 сек..."
                )
                time.sleep(30)


if __name__ == "__main__":
    bot = MartingaleBot()
    bot.run()
