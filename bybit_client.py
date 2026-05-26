import time
import logging
from pybit.unified_trading import HTTP
from config import (
    BYBIT_API_KEY, BYBIT_API_SECRET, CATEGORY,
    API_MAX_RETRIES, API_RETRY_DELAY,
    MAX_SLIPPAGE_PCT, PRICE_PRECISION, COMMISSION_PCT,
    FUNDING_HOURS, DEFAULT_FUNDING_RATE
)

logger = logging.getLogger(__name__)


class BybitClient:
    def __init__(self):
        self.client = HTTP(
            testnet=False,
            api_key=BYBIT_API_KEY,
            api_secret=BYBIT_API_SECRET,
            recv_window=10000
        )
        self.min_qty          = 0.1
        self.qty_step         = 0.01
        self._last_funding_hr = -1
        logger.info("✅ Bybit клиент подключён")

    def _retry(self, func, *args, **kwargs):
        for attempt in range(API_MAX_RETRIES):
            try:
                r = func(*args, **kwargs)
                if isinstance(r, dict) and r.get("retCode") not in (0, None):
                    raise Exception(f"[{r['retCode']}] {r.get('retMsg','?')}")
                return r
            except Exception as e:
                wait = API_RETRY_DELAY * (2 ** attempt)
                logger.warning(f"Попытка {attempt+1}/{API_MAX_RETRIES}: {e} → {wait}с")
                if attempt < API_MAX_RETRIES - 1:
                    time.sleep(wait)
        return None

    def auto_setup(self, symbol: str, leverage: int) -> dict:
        out = {}
        try:
            self.client.switch_position_mode(
                category=CATEGORY, coin="USDT", mode=0
            )
            out["mode"] = "One-Way ✅"
        except:
            out["mode"] = "One-Way (уже установлен)"

        try:
            self.client.set_leverage(
                category=CATEGORY, symbol=symbol,
                buyLeverage=str(leverage), sellLeverage=str(leverage)
            )
            out["leverage"] = f"{leverage}x ✅"
        except:
            out["leverage"] = f"{leverage}x (уже)"

        try:
            resp = self.client.get_instruments_info(
                category=CATEGORY, symbol=symbol
            )
            lot = resp["result"]["list"][0]["lotSizeFilter"]
            self.min_qty  = float(lot["minOrderQty"])
            self.qty_step = float(lot["qtyStep"])
            out["min_qty"]  = self.min_qty
            out["qty_step"] = self.qty_step
        except Exception as e:
            logger.error(f"instrument_info: {e}")
            out["error"] = str(e)

        out["ok"] = "error" not in out
        return out

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

    def apply_funding(self, symbol: str) -> dict | None:
        """Проверить и вернуть инфо о фандинге (реальный счёт)."""
        from datetime import datetime, timezone
        now_utc = datetime.now(timezone.utc)
        hour    = now_utc.hour
        if hour not in FUNDING_HOURS or self._last_funding_hr == hour:
            return None
        self._last_funding_hr = hour
        rate = self.get_funding_rate(symbol)
        return {"rate": rate, "hour": hour, "payment": None}

    def get_wallet_balance(self) -> dict:
        try:
            r    = self.client.get_wallet_balance(accountType="UNIFIED")
            coins = r["result"]["list"][0]["coin"]
            usdt = next((c for c in coins if c["coin"] == "USDT"), None)
            if usdt:
                return {
                    "balance":    round(float(usdt["walletBalance"]), 2),
                    "available":  round(float(usdt["availableToWithdraw"]), 2),
                    "unrealised": round(float(usdt.get("unrealisedPnl", 0)), 2)
                }
        except Exception as e:
            logger.error(f"get_wallet_balance: {e}")
        return {"balance": 0, "available": 0, "unrealised": 0}

    def get_position_size(self, symbol: str) -> float:
        try:
            r   = self.client.get_positions(category=CATEGORY, symbol=symbol)
            pos = r["result"]["list"]
            return float(pos[0]["size"]) if pos else 0.0
        except Exception as e:
            logger.error(f"get_position_size: {e}")
            return 0.0

    def place_market_buy(self, symbol: str, qty: float) -> dict | None:
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
        avg_price, filled_qty = self._get_fill(symbol, order_id, qty)

        cur  = self.get_price(symbol) or avg_price
        slip = abs(avg_price - cur) / cur * 100 if cur else 0
        if slip > MAX_SLIPPAGE_PCT:
            logger.warning(f"⚠️ Проскальзывание {slip:.2f}%")

        return {"orderId": order_id, "avg_price": avg_price, "qty": filled_qty}

    def _get_fill(self, symbol, order_id, fallback_qty):
        try:
            r = self.client.get_order_history(
                category=CATEGORY, symbol=symbol, orderId=order_id
            )
            orders = r["result"]["list"]
            if orders and orders[0]["orderStatus"] == "Filled":
                return float(orders[0]["avgPrice"]), float(orders[0]["cumExecQty"])
        except Exception as e:
            logger.warning(f"_get_fill: {e}")
        return self.get_price(symbol) or 0.0, fallback_qty

    def _round_qty(self, qty: float) -> float:
        steps = round(qty / self.qty_step)
        dec   = (
            len(str(self.qty_step).rstrip("0").split(".")[-1])
            if "." in str(self.qty_step) else 0
        )
        return round(steps * self.qty_step, dec)

    def set_take_profit(self, symbol: str, tp_price: float) -> bool:
        r = self._retry(
            self.client.set_trading_stop,
            category=CATEGORY, symbol=symbol,
            takeProfit=str(round(tp_price, PRICE_PRECISION)),
            tpTriggerBy="MarkPrice",
            tpslMode="Full",
            positionIdx=0
        )
        ok = r is not None and r.get("retCode") == 0
        logger.info(f"{'✅' if ok else '❌'} TP @ ${tp_price:.3f}")
        return ok

    def set_stop_loss_backup(self, symbol: str, sl_price: float) -> bool:
        r = self._retry(
            self.client.set_trading_stop,
            category=CATEGORY, symbol=symbol,
            stopLoss=str(round(sl_price, PRICE_PRECISION)),
            slTriggerBy="MarkPrice",
            tpslMode="Full",
            positionIdx=0
        )
        ok = r is not None and r.get("retCode") == 0
        logger.info(f"{'✅' if ok else '❌'} Backup SL @ ${sl_price:.3f}")
        return ok

    def clear_tp_sl(self, symbol: str):
        try:
            self.client.set_trading_stop(
                category=CATEGORY, symbol=symbol,
                takeProfit="0", stopLoss="0", positionIdx=0
            )
        except Exception as e:
            logger.warning(f"clear_tp_sl: {e}")

    def market_close_all(self, symbol: str, qty: float) -> bool:
        qty = self._round_qty(qty)
        r   = self._retry(
            self.client.place_order,
            category=CATEGORY, symbol=symbol,
            side="Sell", orderType="Market",
            qty=str(qty), reduceOnly=True, positionIdx=0
        )
        return r is not None

    def cancel_all_orders(self, symbol: str):
        try:
            self.client.cancel_all_orders(category=CATEGORY, symbol=symbol)
        except Exception as e:
            logger.warning(f"cancel_all_orders: {e}")

    def get_closed_pnl(self, symbol: str) -> dict | None:
        try:
            r   = self.client.get_closed_pnl(
                category=CATEGORY, symbol=symbol, limit=1
            )
            lst = r["result"]["list"]
            if lst:
                p = lst[0]
                return {
                    "pnl":        float(p["closedPnl"]),
                    "exit_price": float(p.get("avgExitPrice", 0)),
                    "qty":        float(p.get("qty", 0))
                }
        except Exception as e:
            logger.error(f"get_closed_pnl: {e}")
        return None
