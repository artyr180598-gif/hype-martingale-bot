import time
import logging
import sys
import os
import json
import threading
import requests
from datetime import datetime
from config import (
    SYMBOL, LEVERAGE, MARGINS,
    ENTRY_DROP_PCT, AVERAGING_STEP_PCT,
    STOP_LOSS_PCT, STOP_LOSS_BACKUP_PCT,
    COMMISSION_PCT, SMART_TP,
    CHECK_INTERVAL, HEARTBEAT_MINUTES,
    PRICE_PRECISION,
    USE_TESTNET, DEMO_MODE, DEMO_BALANCE
)
from bybit_client import BybitClient
from demo_client import DemoClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

STATS_FILE = "stats.json"
STATE_FILE = "state.json"


def atomic_write(fp, data):
    tmp = fp + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, fp)


def load_json(fp, default):
    try:
        if os.path.exists(fp):
            with open(fp) as f:
                return json.load(f)
    except:
        pass
    return default


class MartingaleBot:
    def __init__(self):
        if USE_TESTNET:
            self.bybit = BybitClient()
            self.demo_mode = False
        elif DEMO_MODE:
            self.bybit = DemoClient(DEMO_BALANCE)
            self.demo_mode = True
        else:
            self.bybit = BybitClient()
            self.demo_mode = False

        self.running = True
        self.stats = load_json(STATS_FILE, {
            "total_profit": 0, "total_trades": 0, "total_stops": 0,
            "today_profit": 0, "today_trades": 0,
            "today_date": str(datetime.now().date()), "history": []
        })
        self.start_time = datetime.now()
        self.last_heartbeat = datetime.now()
        self._reset()

    def _reset(self):
        self.in_trade = False
        self.entries = []
        self.current_level = 0
        self.first_entry_price = None
        self.average_price = None
        self.total_qty = 0.0
        self.recent_high = None
        atomic_write(STATE_FILE, {})

    def _mode_label(self):
        if USE_TESTNET:
            return "🧪 ТЕСТНЕТ"
        return "🎮 ДЕМО" if self.demo_mode else "💰 РЕАЛ"

    def _get_chat_id(self):
        from config import TELEGRAM_CHAT_ID
        return TELEGRAM_CHAT_ID

    def send_keyboard(self, chat_id, text):
        keyboard = {
            "inline_keyboard": [
                [{"text": "📊 Статус", "callback_data": "status"}],
                [{"text": "⏹ Стоп бот", "callback_data": "stop"}]
            ]
        }
        try:
            from config import TELEGRAM_TOKEN
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": text, "reply_markup": keyboard},
                timeout=10
            )
        except:
            pass

    def poll_telegram(self):
        from config import TELEGRAM_TOKEN
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
        offset = 0
        while self.running:
            try:
                resp = requests.get(url, params={"timeout": 20, "offset": offset}, timeout=25).json()
                for upd in resp.get("result", []):
                    offset = upd["update_id"] + 1
                    msg = upd.get("message", {})
                    if msg.get("text"):
                        self._handle_cmd(msg["text"], str(msg["chat"]["id"]))
                    cb = upd.get("callback_query")
                    if cb:
                        try:
                            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery",
                                         json={"callback_query_id": cb["id"]}, timeout=5)
                        except:
                            pass
                        self._handle_cmd("/" + cb["data"], str(cb["message"]["chat"]["id"]))
            except:
                time.sleep(5)

    def _handle_cmd(self, cmd, chat_id):
        if cmd in ("/start", "/status"):
            self.send_keyboard(chat_id, self.get_dashboard())
        elif cmd == "/stop":
            self.running = False
            self.send_keyboard(chat_id, "⏹ Бот остановлен")

    def get_dashboard(self):
        price = self.bybit.get_price(SYMBOL) or 0
        wb = self.bybit.get_wallet_balance()
        return (
            f"╔══════════════════════════╗\n"
            f"║  🚀 HYPE BOT {self._mode_label()}\n"
            f"╠══════════════════════════╣\n"
            f"║ 💲 Цена:      ${price}\n"
            f"║ 📈 Макс:      ${self.recent_high or '—'}\n"
            f"╠══════════════════════════╣\n"
            f"║ 💳 Баланс:    ${wb['balance']}\n"
            f"║ 💵 Доступно:  ${wb['available']}\n"
            f"╚══════════════════════════╝"
        )

    def run(self):
        logger.info(f"Бот запущен | {self._mode_label()} | Плечо {LEVERAGE}x")
        threading.Thread(target=self.poll_telegram, daemon=True).start()
        chat_id = self._get_chat_id()
        self.bybit.auto_setup(SYMBOL, LEVERAGE)
        self.send_keyboard(chat_id, self.get_dashboard())

        while self.running:
            try:
                price = self.bybit.get_price(SYMBOL)
                if price is None:
                    time.sleep(CHECK_INTERVAL)
                    continue

                if not self.in_trade:
                    if self.recent_high is None or price > self.recent_high:
                        self.recent_high = price

                    if self.recent_high and (self.recent_high - price) / self.recent_high * 100 >= ENTRY_DROP_PCT:
                        margin = MARGINS[0]
                        qty = (margin * LEVERAGE) / price
                        result = self.bybit.place_market_buy(SYMBOL, qty)
                        if result:
                            self.first_entry_price = result["avg_price"]
                            self.in_trade = True
                            self.entries = [(result["avg_price"], result["qty"], margin)]
                            self.total_qty = result["qty"]
                            self.average_price = result["avg_price"]
                            self.current_level = 1
                            tp_price = round(self.average_price * (1 + SMART_TP[0] / 100), PRICE_PRECISION)
                            self.bybit.set_take_profit(SYMBOL, tp_price)
                            if not self.demo_mode:
                                sl = round(self.average_price * (1 - STOP_LOSS_BACKUP_PCT / 100), PRICE_PRECISION)
                                self.bybit.set_stop_loss_backup(SYMBOL, sl)
                            logger.info(f"Вход: {result['qty']} @ ${result['avg_price']}")
                            self.send_keyboard(chat_id, self.get_dashboard())
                else:
                    if self.first_entry_price and (self.first_entry_price - price) / self.first_entry_price * 100 >= STOP_LOSS_PCT:
                        self.bybit.cancel_all_orders(SYMBOL)
                        self.bybit.market_close_all(SYMBOL, self.total_qty)
                        logger.info("Стоп-лосс")
                        self._reset()
                        self.recent_high = price
                        time.sleep(120)
                        continue

                    pos_size = self.bybit.get_position_size(SYMBOL)
                    if pos_size == 0:
                        logger.info("Позиция закрыта (TP)")
                        self._reset()
                        self.recent_high = price
                        continue

                time.sleep(CHECK_INTERVAL)

            except KeyboardInterrupt:
                self.running = False
            except Exception as e:
                logger.error(f"Ошибка: {e}")
                time.sleep(30)


if __name__ == "__main__":
    MartingaleBot().run()