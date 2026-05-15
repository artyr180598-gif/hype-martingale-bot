import time
import logging
from pybit.unified_trading import HTTP
from config import (
    BYBIT_API_KEY, BYBIT_API_SECRET, CATEGORY,
    API_MAX_RETRIES, API_RETRY_DELAY,
    MAX_SLIPPAGE_PCT, PRICE_PRECISION
)

logger = logging.getLogger(__name__)


class BybitClient:
    def __init__(self):
        self.client   = HTTP(
            testnet=False,
            api_key=BYBIT_API_KEY,
            api_secret=BYBIT_API_SECRET,
            recv_window=10000
        )
        self.min_qty  = 0.1
        self.qty_step = 0.01
        logger.info("Bybit клиент подключён")

    # ── RETRY ─────────────────────────────────────────────────────
    def _retry(self, func, *args, **kwargs):
        """Повторные попытки с экспоненциальной задержкой"""
        for attempt in range(API_MAX_RETRIES):
            try:
                r = func(*args, **kwargs)
                if isinstance(r, dict) and r.get("retCode") not in (0, None):
                    raise Exception(f"[{r['retCode']}] {r.get('retMsg', '?')}")
                return r
            except Exception as e:
                wait = API_RETRY_DELAY * (2 ** attempt)
                logger.warning(f"Попытка {attempt+1}/{API_MAX_RETRIES}: {e} → ждём {wait}с")
                if attempt < API_MAX_RETRIES - 1:
                    time.sleep(wait)
        logger.error("Все попытки исчерпаны")
        return None

    # ── AUTO SETUP ────────────────────────────────────────────────
    def auto_setup(self, symbol: str, leverage: int) -> dict:
        """Автоматическая настройка Bybit при старте"""
        out = {}

        # 1. One-Way Mode
        try:
            self.client.switch_position_mode(
                category=CATEGORY, coin="USDT", mode=0
            )
            out["mode"] = "One-Way ✅"
        except Exception:
            out["mode"] = "One-Way (уже установлен)"
        logger.info(out["mode"])

        # 2. Плечо
        try:
            self.client.set_leverage(
                category=CATEGORY, symbol=symbol,
                buyLeverage=str(leverage), sellLeverage=str(leverage)
            )
            out["leverage"] = f"{leverage}x ✅"
        except Exception:
            out["leverage"] = f"{leverage}x (уже установлено)"
        logger.info(out["leverage"])

        # 3. Минимальный размер и шаг
        try:
            resp = self.client.get_instruments_info(
                category=CATEGORY, symbol=symbol
            )
            lot = resp["result"]["list"][0]["lotSizeFilter"]
            self.min_qty  = float(lot["minOrderQty"])
            self.qty_step = float(lot["qtyStep"])
            out["min_qty"]  = self.min_qty
            out["qty_step"] = self.qty_step
            logger.info(
                f"{symbol}: min_qty={self.min_qty}, qty_step={self.qty_step} ✅"
            )
        except Exception as e:
            logger.error(f"Instrument info: {e}")
            out["error"] = str(e)

        out["ok"] = "error" not in out
        return out

    # ── ЦЕНА ──────────────────────────────────────────────────────
    def get_price(self, symbol: str) -> float | None:
        try:
            r = self.client.get_tickers(category=CATEGORY, symbol=symbol)
            return float(r["result"]["list"][0]["lastPrice"])
        except Exception as e:
            logger.error(f"get_price: {e}")
            return None

    # ── БАЛАНС ────────────────────────────────────────────────────
    def get_wallet_balance(self) -> dict:
        try:
            r     = self.client.get_wallet_balance(accountType="UNIFIED")
            coins = r["result"]["list"][0]["coin"]
            usdt  = next((c for c in coins if c["coin"] == "USDT"), None)
            if usdt:
                return {
                    "balance":    round(float(usdt["walletBalance"]), 2),
                    "available":  round(float(usdt["availableToWithdraw"]), 2),
                    "unrealised": round(float(usdt.get("unrealisedPnl", 0)), 2)
                }
        except Exception as e:
            logger.error(f"get_wallet_balance: {e}")
        return {"balance": 0, "available": 0, "unrealised": 0}

    # ── ПОЗИЦИЯ ───────────────────────────────────────────────────
    def get_position_size(self, symbol: str) -> float:
        try:
            r   = self.client.get_positions(category=CATEGORY, symbol=symbol)
            pos = r["result"]["list"]
            return float(pos[0]["size"]) if pos else 0.0
        except Exception as e:
            logger.error(f"get_position_size: {e}")
            return 0.0

    # ── ПОКУПКА ───────────────────────────────────────────────────
    def place_market_buy(self, symbol: str, qty: float) -> dict | None:
        qty = self._round_qty(qty)
        if qty < self.min_qty:
            logger.warning(f"qty {qty} < min {self.min_qty}")
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
        time.sleep(0.5)  # Ждём исполнения
        avg_price, filled_qty = self._get_fill(symbol, order_id, qty)

        # Проверка проскальзывания
        cur  = self.get_price(symbol) or avg_price
        slip = abs(avg_price - cur) / cur * 100 if cur else 0
        if slip > MAX_SLIPPAGE_PCT:
            logger.warning(
                f"⚠️ Проскальзывание {slip:.2f}%: "
                f"~${cur:.3f} → ${avg_price:.3f}"
            )

        logger.info(f"✅ BUY {filled_qty} HYPE @ ${avg_price:.3f}")
        return {"orderId": order_id, "avg_price": avg_price, "qty": filled_qty}

    def _get_fill(
        self, symbol: str, order_id: str, fallback_qty: float
    ) -> tuple[float, float]:
        """Реальная цена исполнения из истории ордеров"""
        try:
            r      = self.client.get_order_history(
                category=CATEGORY, symbol=symbol, orderId=order_id
            )
            orders = r["result"]["list"]
            if orders and orders[0]["orderStatus"] == "Filled":
                return (
                    float(orders[0]["avgPrice"]),
                    float(orders[0]["cumExecQty"])
                )
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

    # ── ТЕЙК-ПРОФИТ ───────────────────────────────────────────────
    def set_take_profit(self, symbol: str, tp_price: float) -> bool:
        """TP через официальный Bybit TP/SL с триггером MarkPrice"""
        r  = self._retry(
            self.client.set_trading_stop,
            category=CATEGORY, symbol=symbol,
            takeProfit=str(round(tp_price, PRICE_PRECISION)),
            tpTriggerBy="MarkPrice",
            tpslMode="Full",
            positionIdx=0
        )
        ok = r is not None and r.get("retCode") == 0
        logger.info(f"{'✅' if ok else '❌'} TP @ ${tp_price:.3f} MarkPrice")
        return ok

    # ── СТРАХОВОЧНЫЙ СТОП ─────────────────────────────────────────
    def set_stop_loss_backup(self, symbol: str, sl_price: float) -> bool:
        """Backup SL на Bybit — сработает даже если бот упал"""
        r  = self._retry(
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
        """Снять TP и SL с позиции"""
        try:
            self.client.set_trading_stop(
                category=CATEGORY, symbol=symbol,
                takeProfit="0", stopLoss="0", positionIdx=0
            )
        except Exception as e:
            logger.warning(f"clear_tp_sl: {e}")

    # ── ЗАКРЫТИЕ ──────────────────────────────────────────────────
    def market_close_all(self, symbol: str, qty: float) -> bool:
        qty = self._round_qty(qty)
        r   = self._retry(
            self.client.place_order,
            category=CATEGORY, symbol=symbol,
            side="Sell", orderType="Market",
            qty=str(qty), reduceOnly=True, positionIdx=0
        )
        ok = r is not None
        logger.info(f"{'✅' if ok else '❌'} Закрытие {qty} HYPE")
        return ok

    def cancel_all_orders(self, symbol: str):
        try:
            self.client.cancel_all_orders(category=CATEGORY, symbol=symbol)
            logger.info("✅ Все ордера отменены")
        except Exception as e:
            logger.warning(f"cancel_all_orders: {e}")

    # ── РЕАЛЬНЫЙ P&L ──────────────────────────────────────────────
    def get_closed_pnl(self, symbol: str) -> dict | None:
        """Реальный P&L последней закрытой сделки"""
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
