import logging
from pybit.unified_trading import HTTP
from config import BYBIT_API_KEY, BYBIT_API_SECRET, CATEGORY

logger = logging.getLogger(__name__)

class BybitClient:
    def __init__(self):
        self.client = HTTP(
            testnet=False,
            api_key=BYBIT_API_KEY,
            api_secret=BYBIT_API_SECRET
        )
        logger.info("Bybit клиент подключён")

    def get_price(self, symbol: str) -> float | None:
        try:
            resp = self.client.get_tickers(category=CATEGORY, symbol=symbol)
            return float(resp["result"]["list"][0]["lastPrice"])
        except Exception as e:
            logger.error(f"get_price: {e}")
            return None

    def set_leverage(self, symbol: str, leverage: int):
        try:
            self.client.set_leverage(
                category=CATEGORY,
                symbol=symbol,
                buyLeverage=str(leverage),
                sellLeverage=str(leverage)
            )
            logger.info(f"Плечо {leverage}x установлено")
        except Exception as e:
            logger.warning(f"set_leverage: {e}")

    def get_position_size(self, symbol: str) -> float:
        try:
            resp = self.client.get_positions(category=CATEGORY, symbol=symbol)
            positions = resp["result"]["list"]
            if positions:
                return float(positions[0]["size"])
            return 0.0
        except Exception as e:
            logger.error(f"get_position_size: {e}")
            return 0.0

    def place_market_buy(self, symbol: str, qty: float) -> dict | None:
        try:
            resp = self.client.place_order(
                category=CATEGORY,
                symbol=symbol,
                side="Buy",
                orderType="Market",
                qty=str(qty),
                positionIdx=0
            )
            if resp["retCode"] == 0:
                logger.info(f"Маркет BUY {qty} {symbol} — OK")
                return resp["result"]
            logger.error(f"place_market_buy: {resp['retCode']} — {resp['retMsg']}")
            return None
        except Exception as e:
            logger.error(f"place_market_buy exception: {e}")
            return None

    def place_limit_sell(self, symbol: str, qty: float, price: float) -> dict | None:
        try:
            resp = self.client.place_order(
                category=CATEGORY,
                symbol=symbol,
                side="Sell",
                orderType="Limit",
                qty=str(qty),
                price=str(price),
                reduceOnly=True,
                positionIdx=0
            )
            if resp["retCode"] == 0:
                logger.info(f"Лимит SELL {qty} @ {price} — OK")
                return resp["result"]
            logger.error(f"place_limit_sell: {resp['retCode']} — {resp['retMsg']}")
            return None
        except Exception as e:
            logger.error(f"place_limit_sell exception: {e}")
            return None

    def market_close_all(self, symbol: str, qty: float):
        try:
            self.client.place_order(
                category=CATEGORY,
                symbol=symbol,
                side="Sell",
                orderType="Market",
                qty=str(qty),
                reduceOnly=True,
                positionIdx=0
            )
            logger.info(f"Закрытие {qty} {symbol}")
        except Exception as e:
            logger.error(f"market_close_all: {e}")

    def cancel_order(self, symbol: str, order_id: str):
        try:
            self.client.cancel_order(
                category=CATEGORY,
                symbol=symbol,
                orderId=order_id
            )
            logger.info(f"Ордер {order_id} отменён")
        except Exception as e:
            logger.warning(f"cancel_order: {e}")

    def cancel_all_orders(self, symbol: str):
        try:
            self.client.cancel_all_orders(category=CATEGORY, symbol=symbol)
            logger.info(f"Все ордера {symbol} отменены")
        except Exception as e:
            logger.warning(f"cancel_all_orders: {e}")
