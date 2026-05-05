import time
import logging
import sys
from config import (
    SYMBOL, LEVERAGE, MARGINS,
    ENTRY_DROP_PCT, AVERAGING_STEP_PCT,
    TAKE_PROFIT_PCT, STOP_LOSS_PCT,
    CHECK_INTERVAL, QTY_PRECISION, PRICE_PRECISION
)
from bybit_client import BybitClient
from notifier import TelegramNotifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)


class MartingaleBot:

    def __init__(self):
        self.bybit    = BybitClient()
        self.notifier = TelegramNotifier()
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
        drop = (self.recent_high - price) / self.recent_high * 100
        return drop >= ENTRY_DROP_PCT

    def _should_average(self, price):
        if not self.in_trade or self.current_level >= len(MARGINS):
            return False
        required = self.current_level * AVERAGING_STEP_PCT
        return self._drop_pct(price) >= required

    def _should_stop(self, price):
        if not self.in_trade:
            return False
        return self._drop_pct(price) >= STOP_LOSS_PCT

    def _open_level(self, price):
        level  = self.current_level
        margin = MARGINS[level]
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
        tp_price = self._round_price(
            self.average_price * (1 + TAKE_PROFIT_PCT / 100)
        )
        result = self.bybit.place_limit_sell(SYMBOL, self.total_qty, tp_price)
        if result:
            self.tp_order_id = result.get("orderId")
            return tp_price
        return None

    def _check_tp_hit(self):
        if not self.in_trade:
            return False
        return self.bybit.get_position_size(SYMBOL) == 0.0

    def _close_all_stop(self):
        self.bybit.cancel_all_orders(SYMBOL)
        self.bybit.market_close_all(SYMBOL, self.total_qty)

    def run(self):
        logger.info("HYPE Мартингейл Бот запущен")
        self.notifier.send(
            "🤖 HYPE Мартингейл Бот запущен!\n\n"
            f"📊 Монета: {SYMBOL}\n"
            f"⚡ Плечо: {LEVERAGE}x\n"
            f"📈 Уровней: {len(MARGINS)}\n"
            f"💰 Маржи: {MARGINS}\n"
            f"🎯 ТП: +{TAKE_PROFIT_PCT}%\n"
            f"🔴 Стоп: -{STOP_LOSS_PCT}%\n\n"
            "👀 Слежу за ценой HYPE..."
        )
        self.bybit.set_leverage(SYMBOL, LEVERAGE)

        while True:
            try:
                price = self.bybit.get_price(SYMBOL)
                if price is None:
                    time.sleep(CHECK_INTERVAL)
                    continue

                if not self.in_trade:
                    if self.recent_high is None or price > self.recent_high:
                        self.recent_high = price

                    if self._should_enter(price):
                        logger.info(f"ВХОД @ ${price}")
                        if self._open_level(price):
                            self.first_entry_price = price
                            self.in_trade = True
                            tp = self._update_tp()
                            drop = round(
                                (self.recent_high - price) / self.recent_high * 100, 2
                            )
                            self.notifier.send(
                                f"🟢 ВХОД 1 открыт\n\n"
                                f"💲 Цена: ${price}\n"
                                f"📉 Откат: -{drop}%\n"
                                f"💵 Маржа: ${MARGINS[0]}\n"
                                f"📦 Куплено: {self.entries[-1][1]} HYPE\n"
                                f"🎯 ТП: ${tp}"
                            )
                else:
                    drop = round(self._drop_pct(price), 2)

                    if self._should_stop(price):
                        logger.warning(f"СТОП-ЛОСС @ ${price}")
                        self._close_all_stop()
                        self.notifier.send(
                            f"🔴 СТОП-ЛОСС!\n\n"
                            f"💲 Цена: ${price}\n"
                            f"📉 Падение: -{drop}%\n"
                            f"💸 Вложено: ${self._total_invested()}\n"
                            f"📊 Уровней: {len(self.entries)}\n"
                            f"⏳ Пауза 2 минуты..."
                        )
                        self._reset()
                        self.recent_high = price
                        time.sleep(120)
                        continue

                    if self._check_tp_hit():
                        invested = self._total_invested()
                        profit   = round(
                            invested * LEVERAGE * TAKE_PROFIT_PCT / 100, 2
                        )
                        self.notifier.send(
                            f"✅ ТЕЙК-ПРОФИТ!\n\n"
                            f"📊 Уровней: {len(self.entries)}\n"
                            f"💵 Вложено: ${invested}\n"
                            f"💰 Прибыль: ~+${profit}\n\n"
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
                        tp = self._update_tp()
                        self.notifier.send(
                            f"📉 УСРЕДНЕНИЕ — Уровень {len(self.entries)}\n\n"
                            f"💲 Цена: ${price} (-{drop}%)\n"
                            f"💵 Добавлено: ${MARGINS[self.current_level-1]}\n"
                            f"📊 Вложено: ${self._total_invested()}\n"
                            f"📈 Средняя: ${self.average_price}\n"
                            f"🎯 Новый ТП: ${tp}\n"
                            f"📦 Всего HYPE: {self.total_qty}\n"
                            f"⚠️ Осталось уровней: {len(MARGINS) - self.current_level}"
                        )

                time.sleep(CHECK_INTERVAL)

            except KeyboardInterrupt:
                self.notifier.send("⏹ Бот остановлен")
                sys.exit(0)
            except Exception as e:
                logger.error(f"Ошибка: {e}")
                self.notifier.send(f"⚠️ Ошибка:\n{e}\n\nПродолжаю через 30 сек...")
                time.sleep(30)


if __name__ == "__main__":
    bot = MartingaleBot()
    bot.run()
