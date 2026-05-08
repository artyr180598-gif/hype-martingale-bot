import logging
from pybit.unified_trading import HTTP
from config import BYBIT_API_KEY, BYBIT_API_SECRET, CATEGORY, COMMISSION_PCT

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
        self.orders          = {}
        self._order_counter  = 0
        self.total_commission = 0.0
        logger.info(f"ДЕМО клиент | Баланс: ${balance}")

    def get_price(self, symbol: str) -> float | None:
        try:
            resp = self.client.get_tickers(category=CATEGORY, symbol=symbol)
            return float(resp["result"]["list"][0]["lastPrice"])
        except Exception as e:
            logger.error(f"get_price: {e}")
            return None

    def set_leverage(self, symbol: str, leverage: int):
        logger.info(f"[ДЕМО] Плечо {leverage}x установлено")

    def get_position_size(self, symbol: str) -> float:
        return self.position_size

    def place_market_buy(self, symbol: str, qty: float) -> dict | None:
        price = self.get_price(symbol)
        if not price:
            return None

        cost       = (qty * price) / 20
        commission = qty * price * COMMISSION_PCT / 100
        total_cost = cost + commission

        if total_cost > self.balance:
            logger.warning(
                f"[ДЕМО] Недостаточно баланса! "
                f"Нужно ${total_cost:.2f}, есть ${self.balance:.2f}"
            )
            return None

        self.balance          -= total_cost
        self.total_commission += commission
        self.position_size     = round(self.position_size + qty, 2)
        self.avg_entry_price   = price
        logger.info(
            f"[ДЕМО] BUY {qty} @ ${price} "
            f"| Комиссия: ${commission:.3f} "
            f"| Баланс: ${self.balance:.2f}"
        )
        return {"orderId": f"demo_{self._order_counter}"}

    def place_limit_sell(self, symbol: str, qty: float, price: float) -> dict | None:
        self._order_counter += 1
        order_id = f"demo_tp_{self._order_counter}"
        self.orders[order_id] = {"price": price, "qty": qty}
        logger.info(f"[ДЕМО] ТП @ ${price} на {qty} HYPE | id={order_id}")
        return {"orderId": order_id}

    def check_tp_triggered(self, current_price: float) -> tuple[bool, float]:
        for order_id, order in list(self.orders.items()):
            if current_price >= order["price"]:
                tp_price   = order["price"]
                qty        = order["qty"]
                commission = qty * tp_price * COMMISSION_PCT / 100
                profit     = qty * (tp_price - self.avg_entry_price)
                margin_back = (qty * self.avg_entry_price) / 20

                self.balance          += margin_back + profit - commission
                self.total_commission += commission
                self.position_size     = 0.0
                self.orders.clear()

                logger.info(
                    f"[ДЕМО] ТП @ ${tp_price} "
                    f"| Прибыль: +${profit:.2f} "
                    f"| Комиссия: ${commission:.3f} "
                    f"| Баланс: ${self.balance:.2f}"
                )
                return True, tp_price
        return False, 0.0

    def market_close_all(self, symbol: str, qty: float):
        price = self.get_price(symbol)
        if not price:
            return
        commission  = qty * price * COMMISSION_PCT / 100
        loss        = qty * (price - self.avg_entry_price)
        margin_back = (qty * self.avg_entry_price) / 20

        self.balance          += margin_back + loss - commission
        self.total_commission += commission
        self.position_size     = 0.0
        self.orders.clear()

        logger.info(
            f"[ДЕМО] СТОП @ ${price} "
            f"| PnL: ${loss:.2f} "
            f"| Баланс: ${self.balance:.2f}"
        )

    def cancel_order(self, symbol: str, order_id: str):
        if order_id in self.orders:
            del self.orders[order_id]

    def cancel_all_orders(self, symbol: str):
        self.orders.clear()

    def get_balance_info(self) -> dict:
        pnl     = round(self.balance - self.initial_balance, 2)
        pnl_pct = round(pnl / self.initial_balance * 100, 2)
        return {
            "balance":    round(self.balance, 2),
            "initial":    self.initial_balance,
            "pnl":        pnl,
            "pnl_pct":    pnl_pct,
            "commission": round(self.total_commission, 2)
        }
