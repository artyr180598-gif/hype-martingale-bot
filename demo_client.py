import logging
import os
import json
from pybit.unified_trading import HTTP
from config import (
    BYBIT_API_KEY, BYBIT_API_SECRET,
    CATEGORY, COMMISSION_PCT, LEVERAGE,
    DEMO_BALANCE, DEMO_STATE_FILE,
    FUNDING_HOURS, DEFAULT_FUNDING_RATE
)
from storage import redis_set, redis_get, redis_available

logger = logging.getLogger(__name__)


def _atomic_write(filepath: str, data: dict):
    tmp = filepath + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, filepath)


class DemoClient:
    """
    Правильный расчёт баланса:
    Вход:  balance -= маржа + комиссия_входа
    Выход: balance += маржа + net_pnl
    Прибыль = только реальная разница цен
    """

    def __init__(self, balance: float):
        self.client = HTTP(
            testnet=False,
            api_key=BYBIT_API_KEY,
            api_secret=BYBIT_API_SECRET
        )

        saved = self._load_demo_state()
        if saved:
            self.balance          = saved["balance"]
            self.initial_balance  = saved["initial_balance"]
            self.total_commission = saved["total_commission"]
            self.total_funding    = saved["total_funding"]
            logger.info(
                f"🎮 ДЕМО восстановлен | "
                f"Баланс: ${self.balance:,.2f} | "
                f"PnL: ${self.balance - self.initial_balance:+,.2f}"
            )
        else:
            self.balance          = balance
            self.initial_balance  = balance
            self.total_commission = 0.0
            self.total_funding    = 0.0
            self._save_demo_state()
            logger.info(f"🎮 ДЕМО новый | Баланс: ${balance:,.2f}")

        self.position_size    = 0.0
        self.avg_entry_price  = 0.0
        self.total_invested   = 0.0
        self.tp_price         = None
        self._last_close_pnl  = None
        self._order_counter   = 0
        self._last_funding_hr = -1

    # ── СОХРАНЕНИЕ ────────────────────────────────────────────────

    def _save_demo_state(self):
        data = {
            "balance":          round(self.balance, 6),
            "initial_balance":  self.initial_balance,
            "total_commission": round(self.total_commission, 6),
            "total_funding":    round(self.total_funding, 6)
        }
        # Сохраняем в Redis (главный)
        if redis_available():
            redis_set("demo_state", data)
        # Дублируем в файл (запасной)
        _atomic_write(DEMO_STATE_FILE, data)

    def _load_demo_state(self) -> dict | None:
        # Сначала Redis
        if redis_available():
            data = redis_get("demo_state")
            if data:
                logger.info(
                    f"☁️ Баланс из Redis: ${data['balance']:,.2f}"
                )
                return data
        # Потом файл
        try:
            if os.path.exists(DEMO_STATE_FILE):
                with open(DEMO_STATE_FILE, encoding="utf-8") as f:
                    data = json.load(f)
                    logger.info(
                        f"📁 Баланс из файла: ${data['balance']:,.2f}"
                    )
                    return data
        except Exception as e:
            logger.warning(f"Загрузка demo_state: {e}")
        return None

    # ── SETUP ─────────────────────────────────────────────────────

    def auto_setup(self, symbol: str, leverage: int) -> dict:
        return {
            "mode":     "One-Way ✅ (ДЕМО)",
            "leverage": f"{leverage}x ✅ (ДЕМО)",
            "min_qty":  0.1,
            "qty_step": 0.01,
            "ok":       True
        }

    def set_take_profit(self, symbol: str, tp_price: float) -> bool:
        self.tp_price = tp_price
        return True

    def set_stop_loss_backup(self, symbol: str, sl_price: float) -> bool:
        return True

    def clear_tp_sl(self, symbol: str):
        self.tp_price = None

    def cancel_all_orders(self, symbol: str):
        self.tp_price = None

    # ── ЦЕНА ──────────────────────────────────────────────────────

    def get_price(self, symbol: str) -> float | None:
        try:
            r = self.client.get_tickers(category=CATEGORY, symbol=symbol)
            return float(r["result"]["list"][0]["lastPrice"])
        except Exception as e:
            logger.error(f"get_price: {e}")
            return None

    def get_funding_rate(self, symbol: str) -> float:
        try:
            r = self.client.get_tickers(category=CATEGORY, symbol=symbol)
            return float(
                r["result"]["list"][0].get("fundingRate", DEFAULT_FUNDING_RATE)
            )
        except:
            return DEFAULT_FUNDING_RATE

    def get_position_size(self, symbol: str) -> float:
        return self.position_size

    def get_liquidation_price(self, symbol: str = None) -> float | None:
        """Оценка цены ликвидации длинной позиции (cross, one-way).

        Ликвидация при: equity(price) <= maintenance_margin
          equity   = свободный баланс + занятая маржа + нереал. PnL
                   = balance + total_invested + qty*(price - avg)
          maint    = qty * price * MMR
        Отсюда:  price <= (qty*avg - (balance + invested)) / (qty*(1 - MMR))
        """
        from config import MAINTENANCE_MARGIN_RATE
        qty = self.position_size
        avg = self.avg_entry_price
        if qty <= 0 or avg <= 0:
            return None
        denom = qty * (1 - MAINTENANCE_MARGIN_RATE)
        if denom <= 0:
            return None
        liq = (qty * avg - (self.balance + self.total_invested)) / denom
        return round(liq, 4) if liq > 0 else None

    def liquidate(self, liq_price: float):
        """Принудительная ликвидация: теряем всю занятую маржу."""
        self._do_close(liq_price)

    # ── ФАНДИНГ ───────────────────────────────────────────────────

    def apply_funding(self, symbol: str) -> dict | None:
        from datetime import datetime, timezone
        now_utc = datetime.now(timezone.utc)
        hour    = now_utc.hour

        if (hour not in FUNDING_HOURS
                or self._last_funding_hr == hour
                or self.position_size == 0):
            return None

        self._last_funding_hr = hour
        rate            = self.get_funding_rate(symbol)
        position_value  = self.position_size * self.avg_entry_price
        funding_payment = position_value * rate

        self.balance       -= funding_payment
        self.total_funding += funding_payment
        self._save_demo_state()

        logger.info(
            f"[ДЕМО] 🌊 Фандинг: "
            f"ставка={rate*100:.4f}% | "
            f"сумма=${funding_payment:.4f} | "
            f"баланс=${self.balance:.2f}"
        )

        return {
            "rate":    rate,
            "payment": round(funding_payment, 4),
            "hour":    hour
        }

    # ── ПОКУПКА ───────────────────────────────────────────────────

    def place_market_buy(self, symbol: str, qty: float) -> dict | None:
        price = self.get_price(symbol)
        if not price:
            return None

        margin     = round((qty * price) / LEVERAGE, 4)
        entry_comm = round(qty * price * COMMISSION_PCT / 100, 4)
        total_cost = margin + entry_comm

        if total_cost > self.balance:
            logger.warning(
                f"[ДЕМО] Недостаточно: "
                f"нужно ${total_cost:.2f}, "
                f"есть ${self.balance:.2f}"
            )
            return None

        # Средневзвешенная цена входа
        if self.position_size > 0 and self.avg_entry_price > 0:
            old_val = self.position_size * self.avg_entry_price
            new_val = qty * price
            self.avg_entry_price = (
                (old_val + new_val) / (self.position_size + qty)
            )
        else:
            self.avg_entry_price = price

        self.position_size  += qty
        self.total_invested += margin

        # Вычитаем маржу + комиссию
        self.balance          -= total_cost
        self.total_commission += entry_comm

        self._order_counter += 1
        self._save_demo_state()

        logger.info(
            f"[ДЕМО] BUY {qty:.2f} @ ${price:.3f} | "
            f"Маржа: ${margin:.2f} | "
            f"Комиссия: ${entry_comm:.3f} | "
            f"Баланс: ${self.balance:.2f}"
        )

        return {
            "orderId":   f"demo_{self._order_counter}",
            "avg_price": price,
            "qty":       qty
        }

    # ── TP ────────────────────────────────────────────────────────

    def check_tp_triggered(self, current_price: float) -> tuple[bool, float]:
        if self.tp_price is None:
            return False, 0.0
        if current_price >= self.tp_price:
            tp = self.tp_price
            self._do_close(tp)
            return True, tp
        return False, 0.0

    # ── ЗАКРЫТИЕ ──────────────────────────────────────────────────

    def market_close_all(self, symbol: str, qty: float) -> bool:
        price = self.get_price(symbol)
        if not price:
            return False
        self._do_close(price)
        return True

    def _do_close(self, close_price: float):
        """
        Правильный расчёт:
        gross_pnl = (выход - вход) × количество
        close_comm = количество × цена_выхода × 0.1%
        net_pnl = gross_pnl - close_comm
        balance += маржа + net_pnl
        """
        qty = self.position_size
        if qty == 0:
            return

        gross_pnl        = round(qty * (close_price - self.avg_entry_price), 4)
        close_commission = round(qty * close_price * COMMISSION_PCT / 100, 4)
        net_pnl          = round(gross_pnl - close_commission, 4)
        entry_commission = round(
            qty * self.avg_entry_price * COMMISSION_PCT / 100, 3
        )

        # Возвращаем маржу + чистая прибыль
        self.balance          += self.total_invested + net_pnl
        self.total_commission += close_commission

        self._last_close_pnl = {
            "pnl":              round(net_pnl, 2),
            "exit_price":       close_price,
            "qty":              qty,
            "gross_pnl":        round(gross_pnl, 2),
            "open_commission":  entry_commission,
            "close_commission": round(close_commission, 3),
            "total_commission": round(entry_commission + close_commission, 3),
            "net_after_all_comm": round(
                gross_pnl - entry_commission - close_commission, 2
            )
        }

        self._save_demo_state()

        logger.info(
            f"[ДЕМО] Закрытие @ ${close_price:.3f} | "
            f"Gross: ${gross_pnl:.2f} | "
            f"Комиссия: ${close_commission:.3f} | "
            f"Net PnL: ${net_pnl:.2f} | "
            f"Баланс: ${self.balance:.2f}"
        )

        self.position_size   = 0.0
        self.avg_entry_price = 0.0
        self.total_invested  = 0.0
        self.tp_price        = None

    # ── CLOSED PNL ────────────────────────────────────────────────

    def get_closed_pnl(self, symbol: str) -> dict | None:
        p = self._last_close_pnl
        self._last_close_pnl = None
        return p

    # ── БАЛАНС ────────────────────────────────────────────────────

    def get_wallet_balance(self) -> dict:
        return self.get_balance_info()

    def get_balance_info(self) -> dict:
        pnl     = round(self.balance - self.initial_balance, 2)
        pnl_pct = round(pnl / self.initial_balance * 100, 2)
        return {
            "balance":    round(self.balance, 2),
            "available":  round(self.balance, 2),
            "initial":    self.initial_balance,
            "invested":   round(self.total_invested, 2),
            "pnl":        pnl,
            "pnl_pct":    pnl_pct,
            "commission": round(self.total_commission, 2),
            "funding":    round(self.total_funding, 4)
        }
