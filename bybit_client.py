import time
import logging
from pybit.unified_trading import HTTP
from config import (
    BYBIT_API_KEY, BYBIT_API_SECRET, CATEGORY,
    API_MAX_RETRIES, API_RETRY_DELAY,
    MAX_SLIPPAGE_PCT, PRICE_PRECISION,
    USE_TESTNET
)

logger = logging.getLogger(__name__)


class BybitClient:
    def __init__(self):
        self.client = HTTP(
            testnet=USE_TESTNET,
            api_key=BYBIT_API_KEY,
            api_secret=BYBIT_API_SECRET,
            recv_window=10000
        )
        self.min_qty  = 0.1
        self.qty_step = 0.01
        logger.info(f"Bybit клиент подключён (testnet={USE_TESTNET})")

    def _retry(self, func, *args, **kwargs):
        for attempt in range(API_MAX_RETRIES):
            try:
                r = func(*args, **kwargs)
                if isinstance(r, dict) and r.get("retCode") not in (0, None):
                    raise Exception(f"[{r['retCode']}] {r.get('retMsg', '?')}")
                return r
            except Exception as e:
                wait = API_RETRY_DELAY * (2 ** attempt)
                logger.warning(f"Попытка {attempt+1}: {e}")
                if attempt < API_MAX_RETRIES - 1:
                    time.sleep(wait)
        return None

    def auto_setup(self, symbol: str, leverage: int) -> dict:
        out = {}
        try:
            self.client.switch_position_mode(category=CATEGORY, coin="USDT", mode=0)
            out["mode"] = "One-Way"
        except Exception:
            out["mode"] = "One-Way (уже)"
        try:
            self.client.set_leverage(
                category=CATEGORY, symbol=symbol,
                buyLeverage=str(leverage), sellLeverage=str(leverage)
            )
            out["leverage"] = f"{leverage}x"
        except Exception:
            out["leverage"] = f"{leverage}x (уже)"
        try:
            resp = self.client.get_instruments_info(category=CATEGORY, symbol=symbol)
            lot = resp["result"]["list"][0]["lotSizeFilter"]
            self.min_qty  = float(lot["minOrderQty"])
            self.qty_step = float(lot["qtyStep"])
            out["min_qty"] = self.min_qty
        except Exception as e:
            out["error"] = str(e)
        return out

    def get_price(self, symbol: str):
        try:
            r = self.client.get_tickers(category=CATEGORY, symbol=symbol)
            return float(r["result"]["list"][0]["lastPrice"])
        except:
            return None

    def get_wallet_balance(self):
        try:
            r = self.client.get_wallet_balance(accountType="UNIFIED")
            coins = r["result"]["list"][0]["coin"]
            usdt = next((c for c in coins if c["coin"] == "USDT"), None)
            if usdt:
                return {
                    "balance": round(float(usdt["walletBalance"]), 2),
                    "available": round(float(usdt.get("availableToTrade", 0)), 2)
                }
        except:
            pass
        return {"balance": 0, "available": 0}

    def get_position_size(self, symbol: str):
        try:
            r = self.client.get_positions(category=CATEGORY, symbol=symbol)
            pos = r["result"]["list"]
            return float(pos[0]["size"]) if pos else 0.0
        except:
            return 0.0

    def place_market_buy(self, symbol: str, qty: float):
        qty = self._round_qty(qty)
        if qty < self.min_qty:
            return None
        r = self._retry(
            self.client.place_order,
            category=CATEGORY, symbol=symbol,
            side="Buy", orderType="Market",
            qty=str(qty), positionIdx=0
        )
        if not r:
            return None
        order_id = r["result"]["orderId"]
        time.sleep(0.5)
        price = self.get_price(symbol) or 0
        return {"orderId": order_id, "avg_price": price, "qty": qty}

    def _round_qty(self, qty: float):
        steps = round(qty / self.qty_step)
        dec = len(str(self.qty_step).rstrip("0").split(".")[-1]) if "." in str(self.qty_step) else 0
        return round(steps * self.qty_step, dec)

    def set_take_profit(self, symbol: str, tp_price: float):
        r = self._retry(
            self.client.set_trading_stop,
            category=CATEGORY, symbol=symbol,
            takeProfit=str(round(tp_price, PRICE_PRECISION)),
            tpTriggerBy="MarkPrice", tpslMode="Full", positionIdx=0
        )
        return r is not None

    def set_stop_loss_backup(self, symbol: str, sl_price: float):
        r = self._retry(
            self.client.set_trading_stop,
            category=CATEGORY, symbol=symbol,
            stopLoss=str(round(sl_price, PRICE_PRECISION)),
            slTriggerBy="MarkPrice", tpslMode="Full", positionIdx=0
        )
        return r is not None

    def clear_tp_sl(self, symbol: str):
        try:
            self.client.set_trading_stop(
                category=CATEGORY, symbol=symbol,
                takeProfit="0", stopLoss="0", positionIdx=0
            )
        except:
            pass

    def market_close_all(self, symbol: str, qty: float):
        qty = self._round_qty(qty)
        r = self._retry(
            self.client.place_order,
            category=CATEGORY, symbol=symbol,
            side="Sell", orderType="Market",
            qty=str(qty), reduceOnly=True, positionIdx=0
        )
        return r is not None

    def cancel_all_orders(self, symbol: str):
        try:
            self.client.cancel_all_orders(category=CATEGORY, symbol=symbol)
        except:
            pass

    def get_closed_pnl(self, symbol: str):
        try:
            r = self.client.get_closed_pnl(category=CATEGORY, symbol=symbol, limit=1)
            lst = r["result"]["list"]
            if lst:
                return {
                    "pnl": float(lst[0]["closedPnl"]),
                    "exit_price": float(lst[0].get("avgExitPrice", 0)),
                    "qty": float(lst[0].get("qty", 0))
                }
        except:
            pass
        return None

    def get_available_margin(self):
        return self.get_wallet_balance()["available"]