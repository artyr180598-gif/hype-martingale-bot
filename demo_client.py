import logging
from pybit.unified_trading import HTTP
from config import BYBIT_API_KEY, BYBIT_API_SECRET, CATEGORY

logger = logging.getLogger(__name__)


class DemoClient:
    """
    Виртуальный клиент — берёт реальные цены с Bybit
    но все сделки симулирует внутри бота
    """

    def __init__(self, balance: float):
        # Подключаемся к Bybit ТОЛЬКО для получения цены
        self.client = HTTP(
            testnet=False,
            api_key=BYBIT_API_KEY,
            api_secret=BYBIT_API_SECRET
        )
        self.balance          = balance
        self.initial_balance  = balance
        self.position_size    = 0.0
        self.position_price   = 0.0
        self.orders           = {}   # id → {price, qty}
        self._order_counter   = 0
        logger.info(f"ДЕМО клиент запущен | Баланс: ${balance}")

    # ─── РЕАЛЬНАЯ ЦЕНА ───────────────────────────────────────────
    def get_price(self, symbol: str) -> float | None:
        try:
            resp = self.client.get_tickers(category=CATEGORY, symbol=symbol)
            return float(resp["result"]["list"][0]["lastPrice"])
        except Exception as e:
            logger.error(f"get_price: {e}")
            return None

    # ─── ВИРТУАЛЬНОЕ ПЛЕЧО ───────────────────────────────────────
    def set_leverage(self, symbol: str, leverage: int):
        logger.info(f"[ДЕМО] Плечо {leverage}x установлено виртуально")

    # ─── ВИРТУАЛЬНАЯ ПОЗИЦИЯ ─────────────────────────────────────
    def get_position_size(self, symbol: str) -> float:
        return self.position_size

    # ─── ВИРТУАЛЬНАЯ ПОКУПКА ─────────────────────────────────────
    def place_market_buy(self, symbol: str, qty: float) -> dict | None:
        price = self.get_price(symbol)
        if not price:
            return None

        cost = (qty * price) / 10  # маржа = номинал / плечо
        if cost > self.balance:
            logger.warning(f"[ДЕМО] Недостаточно баланса! Нужно ${cost:.2f}, есть ${self.balance:.2f}")
            return None

        self.balance       -= cost
        self.position_size  = round(self.position_size + qty, 2)
        self.position_price = price
        logger.info(f"[ДЕМО] BUY {qty} @ ${price} | Баланс: ${self.balance:.2f}")
        return {"orderId": f"demo_{self._order_counter}"}

    # ─── ВИРТУАЛЬНЫЙ ТП ──────────────────────────────────────────
    def place_limit_sell(self, symbol: str, qty: float, price: float) -> dict | None:
        self._order_counter += 1
        order_id = f"demo_tp_{self._order_counter}"
        self.orders[order_id] = {"price": price, "qty": qty}
        logger.info(f"[ДЕМО] ТП поставлен @ ${price} на {qty} HYPE | id={order_id}")
        return {"orderId": order_id}

    # ─── ПРОВЕРКА СРАБАТЫВАНИЯ ТП ────────────────────────────────
    def check_tp_triggered(self, current_price: float) -> tuple[bool, float]:
        """
        Проверяет сработал ли ТП
        Возвращает (сработал, цена_тп)
        """
        for order_id, order in list(self.orders.items()):
            if current_price >= order["price"]:
                tp_price = order["price"]
                qty      = order["qty"]
                # Начисляем прибыль на баланс
                profit_per_hype = tp_price - self.position_price
                margin_back = (qty * self.position_price) / 10
                profit      = qty * profit_per_hype
                self.balance       += margin_back + profit
                self.position_size  = 0.0
                self.orders.clear()
                logger.info(f"[ДЕМО] ТП сработал @ ${tp_price} | Прибыль: +${profit:.2f} | Баланс: ${self.balance:.2f}")
                return True, tp_price
        return False, 0.0

    # ─── ВИРТУАЛЬНОЕ ЗАКРЫТИЕ (СТОП) ─────────────────────────────
    def market_close_all(self, symbol: str, qty: float):
        price = self.get_price(symbol)
        if not price:
            return
        loss_per_hype  = price - self.position_price
        margin_back    = (qty * self.position_price) / 10
        loss           = qty * loss_per_hype
        self.balance       += margin_back + loss
        self.position_size  = 0.0
        self.orders.clear()
        logger.info(f"[ДЕМО] СТОП закрытие @ ${price} | Изменение: ${loss:.2f} | Баланс: ${self.balance:.2f}")

    def cancel_order(self, symbol: str, order_id: str):
        if order_id in self.orders:
            del self.orders[order_id]
            logger.info(f"[ДЕМО] Ордер {order_id} отменён")

    def cancel_all_orders(self, symbol: str):
        self.orders.clear()
        logger.info("[ДЕМО] Все ордера отменены")

    def get_balance_info(self) -> dict:
        return {
            "balance":   round(self.balance, 2),
            "initial":   self.initial_balance,
            "pnl":       round(self.balance - self.initial_balance, 2),
            "pnl_pct":   round((self.balance - self.initial_balance) / self.initial_balance * 100, 2)
        }
