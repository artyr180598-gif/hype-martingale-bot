import time
import logging
import sys
import requests
import threading
import json
import os
from datetime import datetime
from config import (
    SYMBOL, LEVERAGE, MARGINS,
    ENTRY_DROP_PCT, AVERAGING_STEP_PCT,
    TAKE_PROFIT_PCT, STOP_LOSS_PCT,
    CHECK_INTERVAL, QTY_PRECISION, PRICE_PRECISION,
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


def load_stats():
    try:
        if os.path.exists(STATS_FILE):
            with open(STATS_FILE) as f:
                return json.load(f)
    except:
        pass
    return {
        "total_profit": 0.0,
        "total_trades": 0,
        "total_stops":  0,
        "today_profit": 0.0,
        "today_trades": 0,
        "today_date":   str(datetime.now().date()),
        "history":      []
    }


def save_stats(stats):
    try:
        with open(STATS_FILE, "w") as f:
            json.dump(stats, f)
    except:
        pass


class MartingaleBot:

    def __init__(self):
        self.demo_mode = DEMO_MODE
        if self.demo_mode:
            self.bybit = DemoClient(DEMO_BALANCE)
            logger.info("🎮 ДЕМО РЕЖИМ АКТИВЕН")
        else:
            self.bybit = BybitClient()
            logger.info("💰 РЕАЛЬНЫЙ РЕЖИМ АКТИВЕН")

        self.notifier   = TelegramNotifier()
        self.running    = True
        self.stats      = load_stats()
        self.start_time = datetime.now()
        self._reset()

    def _reset(self):
        self.in_trade          = False
        self.entries           = []
        self.current_level     = 0
        self.first_entry_price = None
        self.average_price     = None
        self.total_qty         = 0.0
        self.tp_order_id       = None
        self.recent_high       = None
        logger.info("Сброс — жду входа")

    def _round_qty(self, qty):
        return round(qty, QTY_PRECISION)

    def _round_price(self, price):
        return round(price, PRICE_PRECISION)

    def _calc_average(self):
        total_cost = sum(p * q for p, q, _ in self.entries)
        total_qty  = sum(q for _, q, _ in self.entries)
        return total_cost / total_qty if total_qty else 0.0

    def _total_invested(self):
        return sum(m for _, _, m in self.entries)

    def _drop_pct(self, price):
        if not self.first_entry_price:
            return 0.0
        return (self.first_entry_price - price) / self.first_entry_price * 100

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

    def _open_level(self, price):
        margin = MARGINS[self.current_level]
        qty    = self._round_qty((margin * LEVERAGE) / price)
        result = self.bybit.place_market_buy(SYMBOL, qty)
        if not result:
            return False
        self.entries.append((price, qty, margin))
        self.total_qty     = self._round_qty(sum(q for _, q, _ in self.entries))
        self.average_price = self._round_price(self._calc_average())
        self.current_level += 1
        return True

    def _update_tp(self):
        if self.tp_order_id:
            self.bybit.cancel_order(SYMBOL, self.tp_order_id)
            self.tp_order_id = None
        tp_pct = SMART_TP[min(self.current_level - 1, len(SMART_TP) - 1)]
        tp_price = self._round_price(
    self.average_price * (1 + tp_pct / 100)
)
        result = self.bybit.place_limit_sell(SYMBOL, self.total_qty, tp_price)
        if result:
            self.tp_order_id = result.get("orderId")
            return tp_price
        return None

    def _check_tp_hit(self, current_price):
        if not self.in_trade:
            return False, 0.0
        if self.demo_mode:
            return self.bybit.check_tp_triggered(current_price)
        else:
            hit = self.bybit.get_position_size(SYMBOL) == 0.0
            tp  = self._round_price(self.average_price * (1 + TAKE_PROFIT_PCT / 100))
            return hit, tp

    def _close_all_stop(self):
        self.bybit.cancel_all_orders(SYMBOL)
        self.bybit.market_close_all(SYMBOL, self.total_qty)

    def _uptime(self):
        delta = datetime.now() - self.start_time
        h = int(delta.total_seconds() // 3600)
        m = int((delta.total_seconds() % 3600) // 60)
        return f"{h}ч {m}м"

    def _check_new_day(self):
        today = str(datetime.now().date())
        if self.stats["today_date"] != today:
            self.stats["today_date"]   = today
            self.stats["today_profit"] = 0.0
            self.stats["today_trades"] = 0
            save_stats(self.stats)

    def _mode_label(self):
        return "🎮 ДЕМО" if self.demo_mode else "💰 РЕАЛ"

    # ─── DASHBOARD ───────────────────────────────────────────────
    def get_dashboard(self):
        price = self.bybit.get_price(SYMBOL)
        if not price:
            return "⚠️ Не могу получить цену"

        self._check_new_day()
        winrate = 0
        if self.stats["total_trades"] > 0:
            wins    = self.stats["total_trades"] - self.stats["total_stops"]
            winrate = round(wins / self.stats["total_trades"] * 100)

        # Баланс
        if self.demo_mode:
            bal = self.bybit.get_balance_info()
            bal_line = (
                f"║ 💳 Баланс:   ${bal['balance']}\n"
                f"║ 📊 PnL:      ${bal['pnl']} ({bal['pnl_pct']}%)\n"
            )
        else:
            bal_line = ""

        if not self.in_trade:
            drop = 0.0
            if self.recent_high:
                drop = round((self.recent_high - price) / self.recent_high * 100, 2)
            need = round(max(ENTRY_DROP_PCT - drop, 0), 2)
            return (
                f"╔══════════════════════════╗\n"
                f"║  🚀 HYPE BOT {self._mode_label()}        ║\n"
                f"╠══════════════════════════╣\n"
                f"║ 💲 Цена:     ${price}\n"
                f"║ 📈 Макс:     ${self.recent_high or '—'}\n"
                f"║ 📉 Откат:    -{drop}%\n"
                f"║ 🎯 До входа: -{need}%\n"
                f"╠══════════════════════════╣\n"
                f"{bal_line}"
                f"║ 📊 СТАТИСТИКА\n"
                f"║ ✅ Сделок сегодня: {self.stats['today_trades']}\n"
                f"║ 💰 Прибыль сегодня: +${round(self.stats['today_profit'], 2)}\n"
                f"║ 💰 Прибыль всего: +${round(self.stats['total_profit'], 2)}\n"
                f"║ ❌ Стопов: {self.stats['total_stops']}\n"
                f"║ 🏆 Винрейт: {winrate}%\n"
                f"╠══════════════════════════╣\n"
                f"║ ⚡ Статус: Жду входа...\n"
                f"║ ⏱ Аптайм: {self._uptime()}\n"
                f"╚══════════════════════════╝"
            )
        else:
            drop = round(self._drop_pct(price), 2)
            tp   = self._round_price(self.average_price * (1 + TAKE_PROFIT_PCT / 100))
            sl   = self._round_price(self.first_entry_price * (1 - STOP_LOSS_PCT / 100))
            pnl  = round((price - self.average_price) * self.total_qty, 2)
            pnl_emoji = "📈" if pnl >= 0 else "📉"
            return (
                f"╔══════════════════════════╗\n"
                f"║  🚀 HYPE BOT {self._mode_label()}        ║\n"
                f"╠══════════════════════════╣\n"
                f"║ 🟢 АКТИВНАЯ СДЕЛКА\n"
                f"║ 💲 Цена:    ${price}\n"
                f"║ 📦 HYPE:    {self.total_qty}\n"
                f"║ 📈 Средняя: ${self.average_price}\n"
                f"║ {pnl_emoji} PnL:     ${pnl}\n"
                f"║ 📉 Падение: -{drop}%\n"
                f"╠══════════════════════════╣\n"
                f"║ 💵 Вложено: ${self._total_invested()}\n"
                f"║ 🎯 ТП:      ${tp}\n"
                f"║ 🔴 СЛ:      ${sl}\n"
                f"║ 📊 Уровень: {len(self.entries)}/{len(MARGINS)}\n"
                f"╠══════════════════════════╣\n"
                f"{bal_line}"
                f"║ 💰 Всего: +${round(self.stats['total_profit'], 2)}\n"
                f"║ ⏱ Аптайм: {self._uptime()}\n"
                f"╚══════════════════════════╝"
            )

    def get_history(self):
        history = self.stats.get("history", [])
        if not history:
            return "📋 История пуста — сделок ещё не было"
        text = f"📋 ПОСЛЕДНИЕ СДЕЛКИ {self._mode_label()}\n\n"
        for t in history[-5:][::-1]:
            emoji = "✅" if t["profit"] > 0 else "❌"
            text += (
                f"{emoji} {t['date']}\n"
                f"   Уровней: {t['levels']} | "
                f"Прибыль: ${t['profit']}\n\n"
            )
        text += f"💰 Всего заработано: +${round(self.stats['total_profit'], 2)}"
        return text

    def get_daily_report(self):
        self._check_new_day()
        winrate = 0
        if self.stats["total_trades"] > 0:
            wins    = self.stats["total_trades"] - self.stats["total_stops"]
            winrate = round(wins / self.stats["total_trades"] * 100)
        if self.demo_mode:
            bal     = self.bybit.get_balance_info()
            bal_str = f"💳 Баланс: ${bal['balance']} (старт ${bal['initial']})\n"
        else:
            bal_str = ""
        return (
            f"📊 ИТОГИ ДНЯ {self._mode_label()} — {datetime.now().strftime('%d.%m.%Y')}\n\n"
            f"{bal_str}"
            f"✅ Сделок сегодня: {self.stats['today_trades']}\n"
            f"💰 Прибыль сегодня: +${round(self.stats['today_profit'], 2)}\n"
            f"💰 Прибыль всего: +${round(self.stats['total_profit'], 2)}\n"
            f"❌ Стопов: {self.stats['total_stops']}\n"
            f"🏆 Винрейт: {winrate}%\n"
            f"⏱ Аптайм: {self._uptime()}"
        )

    # ─── TELEGRAM ────────────────────────────────────────────────
    def send_keyboard(self, chat_id, text):
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
                [
                    {"text": "⏹ Стоп бот", "callback_data": "stop"}
                ]
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

    def answer_callback(self, callback_id):
        try:
            from config import TELEGRAM_TOKEN
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery",
                json={"callback_query_id": callback_id},
                timeout=5
            )
        except:
            pass

    def poll_telegram(self):
        from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
        url    = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
        offset = 0
        while self.running:
            try:
                resp = requests.get(
                    url,
                    params={"timeout": 20, "offset": offset},
                    timeout=25
                ).json()
                for update in resp.get("result", []):
                    offset = update["update_id"] + 1
                    msg = update.get("message", {})
                    if msg.get("text"):
                        self._handle_command(msg["text"], msg["chat"]["id"])
                    cb = update.get("callback_query")
                    if cb:
                        self.answer_callback(cb["id"])
                        self._handle_command("/" + cb["data"], cb["message"]["chat"]["id"])
            except Exception as e:
                logger.error(f"poll_telegram: {e}")
                time.sleep(5)

    def _handle_command(self, cmd, chat_id):
        if cmd in ["/start", "/status"]:
            self.send_keyboard(chat_id, self.get_dashboard())
        elif cmd == "/price":
            price = self.bybit.get_price(SYMBOL)
            self.send_keyboard(chat_id, f"💲 HYPE сейчас: ${price}")
        elif cmd == "/history":
            self.send_keyboard(chat_id, self.get_history())
        elif cmd == "/report":
            self.send_keyboard(chat_id, self.get_daily_report())
        elif cmd == "/stop":
            self.running = False
            self.send_keyboard(chat_id, "⏹ Бот остановлен!\n\nЗапусти снова на Railway")

    # ─── ГЛАВНЫЙ ЦИКЛ ────────────────────────────────────────────
    def run(self):
        logger.info(f"Бот запущен | Режим: {'ДЕМО' if self.demo_mode else 'РЕАЛ'}")

        tg_thread = threading.Thread(target=self.poll_telegram, daemon=True)
        tg_thread.start()

        from config import TELEGRAM_CHAT_ID
        demo_note = f"\n\n🎮 ДЕМО РЕЖИМ\n💳 Виртуальный баланс: ${DEMO_BALANCE}" if self.demo_mode else ""
        self.send_keyboard(
            TELEGRAM_CHAT_ID,
            f"🚀 HYPE Мартингейл Бот запущен!\n\n"
            f"📊 Монета: {SYMBOL}\n"
            f"⚡ Плечо: {LEVERAGE}x\n"
            f"📈 Уровней: {len(MARGINS)}\n"
            f"💰 Маржи: {MARGINS}\n"
            f"🎯 ТП: +{TAKE_PROFIT_PCT}%\n"
            f"🔴 Стоп: -{STOP_LOSS_PCT}%"
            f"{demo_note}\n\n"
            f"👀 Слежу за ценой HYPE..."
        )
        self.bybit.set_leverage(SYMBOL, LEVERAGE)
        last_report_date = str(datetime.now().date())

        while self.running:
            try:
                price = self.bybit.get_price(SYMBOL)
                if price is None:
                    time.sleep(CHECK_INTERVAL)
                    continue

                now   = datetime.now()
                today = str(now.date())
                if now.hour == 23 and now.minute == 0 and last_report_date != today:
                    last_report_date = today
                    self.send_keyboard(TELEGRAM_CHAT_ID, self.get_daily_report())

                if not self.in_trade:
                    if self.recent_high is None or price > self.recent_high:
                        self.recent_high = price
                    if self._should_enter(price):
                        if self._open_level(price):
                            self.first_entry_price = price
                            self.in_trade = True
                            tp   = self._update_tp()
                            drop = round((self.recent_high - price) / self.recent_high * 100, 2)
                            bal  = f"\n💳 Баланс: ${self.bybit.get_balance_info()['balance']}" if self.demo_mode else ""
                            self.send_keyboard(
                                TELEGRAM_CHAT_ID,
                                f"🟢 ВХОД 1 открыт {self._mode_label()}\n\n"
                                f"💲 Цена: ${price}\n"
                                f"📉 Откат: -{drop}%\n"
                                f"💵 Маржа: ${MARGINS[0]}\n"
                                f"📦 Куплено: {self.entries[-1][1]} HYPE\n"
                                f"🎯 ТП: ${tp}"
                                f"{bal}"
                            )
                else:
                    drop     = round(self._drop_pct(price), 2)
                    tp_hit, tp_price = self._check_tp_hit(price)

                    if self._should_stop(price):
                        self._close_all_stop()
                        invested = self._total_invested()
                        loss     = round(invested * 0.05, 2)

                        self.stats["total_stops"]  += 1
                        self.stats["total_trades"] += 1
                        self.stats["total_profit"] -= loss
                        self.stats["today_trades"] += 1
                        self.stats["today_profit"] -= loss
                        self.stats["history"].append({
                            "date":   now.strftime("%d.%m %H:%M"),
                            "levels": len(self.entries),
                            "profit": -loss
                        })
                        save_stats(self.stats)

                        bal = f"\n💳 Баланс: ${self.bybit.get_balance_info()['balance']}" if self.demo_mode else ""
                        self.send_keyboard(
                            TELEGRAM_CHAT_ID,
                            f"🔴 СТОП-ЛОСС {self._mode_label()}\n\n"
                            f"💲 Цена: ${price}\n"
                            f"📉 Падение: -{drop}%\n"
                            f"💸 Вложено: ${invested}\n"
                            f"📊 Уровней: {len(self.entries)}\n"
                            f"❌ Стопов всего: {self.stats['total_stops']}\n"
                            f"⏳ Пауза 2 минуты..."
                            f"{bal}"
                        )
                        self._reset()
                        self.recent_high = price
                        time.sleep(120)
                        continue

                    if tp_hit:
                        invested = self._total_invested()
                        profit   = round(invested * LEVERAGE * TAKE_PROFIT_PCT / 100, 2)
                        levels   = len(self.entries)

                        self.stats["total_trades"] += 1
                        self.stats["total_profit"] += profit
                        self.stats["today_trades"] += 1
                        self.stats["today_profit"] += profit
                        self.stats["history"].append({
                            "date":   now.strftime("%d.%m %H:%M"),
                            "levels": levels,
                            "profit": profit
                        })
                        save_stats(self.stats)

                        bal = f"\n💳 Баланс: ${self.bybit.get_balance_info()['balance']}" if self.demo_mode else ""
                        self.send_keyboard(
                            TELEGRAM_CHAT_ID,
                            f"✅ ТЕЙК-ПРОФИТ {self._mode_label()}\n\n"
                            f"📊 Уровней: {levels}\n"
                            f"💵 Вложено: ${invested}\n"
                            f"💰 Прибыль: +${profit}\n\n"
                            f"✅ Сделок сегодня: {self.stats['today_trades']}\n"
                            f"💰 Прибыль сегодня: +${round(self.stats['today_profit'], 2)}\n"
                            f"💰 Прибыль всего: +${round(self.stats['total_profit'], 2)}"
                            f"{bal}\n\n"
                            f"👀 Ищу следующий вход..."
                        )
                        self._reset()
                        self.recent_high = price
                        continue

                    averaged = False
                    while self._should_average(price) and self.current_level < len(MARGINS):
                        if self._open_level(price):
                            averaged = True
                        else:
                            break

                    if averaged:
                        tp  = self._update_tp()
                        bal = f"\n💳 Баланс: ${self.bybit.get_balance_info()['balance']}" if self.demo_mode else ""
                        self.send_keyboard(
                            TELEGRAM_CHAT_ID,
                            f"📉 УСРЕДНЕНИЕ {self._mode_label()} — Уровень {len(self.entries)}\n\n"
                            f"💲 Цена: ${price} (-{drop}%)\n"
                            f"💵 Добавлено: ${MARGINS[self.current_level-1]}\n"
                            f"📊 Вложено: ${self._total_invested()}\n"
                            f"📈 Средняя: ${self.average_price}\n"
                            f"🎯 Новый ТП: ${tp}\n"
                            f"📦 HYPE: {self.total_qty}\n"
                            f"⚠️ Осталось уровней: {len(MARGINS) - self.current_level}"
                            f"{bal}"
                        )

                time.sleep(CHECK_INTERVAL)

            except KeyboardInterrupt:
                self.send_keyboard(TELEGRAM_CHAT_ID, "⏹ Бот остановлен")
                sys.exit(0)
            except Exception as e:
                logger.error(f"Ошибка: {e}")
                self.send_keyboard(
                    TELEGRAM_CHAT_ID,
                    f"⚠️ Ошибка:\n{e}\n\nПродолжаю через 30 сек..."
                )
                time.sleep(30)


if __name__ == "__main__":
    bot = MartingaleBot()
    bot.run()
