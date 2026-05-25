import logging
from pybit.unified_trading import HTTP
from config import (
    BYBIT_API_KEY, BYBIT_API_SECRET,
    CATEGORY, COMMISSION_PCT, LEVERAGE
)

logger = logging.getLogger(__name__)


class DemoClient:
    def __init__(self, balance: float):
        self.client = HTTP(
            testnet=False,
            api_key=BYBIT_API_KEY,
            api_secret=BYBIT_API_SECRET
        )
        self.balance         = balance
        self.initial_balance = balance
        self.position_size   = 0.0
        self.avg_entry_price = 0.0
        self.total_invested  = 0.0
        self.tp_price        = None

        # Комиссии считаются ТОЛЬКО здесь — один раз
        self.total_commission = 0.0

        self._last_close_pnl = None
        self._order_counter  = 0

        logger.info(f"🎮 ДЕМО клиент | Баланс: ${balance:,.2f}")

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
        logger.info(f"[ДЕМО] TP @ ${tp_price:.3f}")
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

    def get_position_size(self, symbol: str) -> float:
        return self.position_size

    # ── ПОКУПКА ───────────────────────────────────────────────────
    def place_market_buy(self, symbol: str, qty: float) -> dict | None:
        price = self.get_price(symbol)
        if not price:
            return None

        margin     = (qty * price) / LEVERAGE
        commission = qty * price * COMMISSION_PCT / 100

        # Проверяем что хватает на маржу + комиссию
        if (margin + commission) > self.balance:
            logger.warning(
                f"[ДЕМО] Недостаточно: нужно ${margin+commission:.2f}, "
                f"есть ${self.balance:.2f}"
            )
            return None

        # Средневзвешенная цена
        if self.position_size > 0 and self.avg_entry_price > 0:
            old_val              = self.position_size * self.avg_entry_price
            new_val              = qty * price
            self.avg_entry_price = (old_val + new_val) / (self.position_size + qty)
        else:
            self.avg_entry_price = price

        self.position_size   += qty
        self.total_invested  += margin

        # Из баланса списываем только комиссию
        # Маржа вернётся при закрытии
        self.balance         -= commission
        self.total_commission += commission

        self._order_counter += 1

        logger.info(
            f"[ДЕМО] BUY {qty:.2f} @ ${price:.3f} | "
            f"Ср: ${self.avg_entry_price:.3f} | "
            f"Комиссия: ${commission:.3f} | "
            f"Баланс: ${self.balance:.2f}"
        )

        return {
            "orderId":   f"demo_{self._order_counter}",
            "avg_price": price,
            "qty":       qty
        }

    # ── ПРОВЕРКА TP ───────────────────────────────────────────────
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
        """Закрыть позицию — расчёт PnL без двойного учёта комиссий."""
        qty = self.position_size

        # Gross PnL = разница цен × кол-во
        gross_pnl = qty * (close_price - self.avg_entry_price)

        # Комиссия при закрытии
        close_commission = qty * close_price * COMMISSION_PCT / 100

        # Net PnL
        net_pnl = gross_pnl - close_commission

        # Возвращаем маржу + net PnL
        self.balance += self.total_invested + net_pnl

        # Учёт комиссии закрытия
        self.total_commission += close_commission

        self._last_close_pnl = {
            "pnl":        round(net_pnl, 2),
            "exit_price": close_price,
            "qty":        qty,
            "gross_pnl":  round(gross_pnl, 2),
            "commission": round(close_commission, 2)
        }

        logger.info(
            f"[ДЕМО] Закрытие @ ${close_price:.3f} | "
            f"Gross: ${gross_pnl:.2f} | "
            f"Комиссия: ${close_commission:.3f} | "
            f"Net PnL: ${net_pnl:.2f} | "
            f"Баланс: ${self.balance:.2f}"
        )

        # Сброс
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
            "available":  round(self.balance - self.total_invested, 2),
            "initial":    self.initial_balance,
            "invested":   round(self.total_invested, 2),
            "pnl":        pnl,
            "pnl_pct":    pnl_pct,
            "commission": round(self.total_commission, 2)
        }
