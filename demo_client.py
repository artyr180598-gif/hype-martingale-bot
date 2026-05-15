import logging
from pybit.unified_trading import HTTP
from config import BYBIT_API_KEY, BYBIT_API_SECRET, CATEGORY, COMMISSION_PCT, LEVERAGE, USE_TESTNET

logger = logging.getLogger(__name__)


class DemoClient:
    def __init__(self, balance: float):
        self.client = HTTP(testnet=USE_TESTNET, api_key=BYBIT_API_KEY, api_secret=BYBIT_API_SECRET)
        self.balance = balance
        self.initial_balance = balance
        self.position_size = 0.0
        self.avg_entry_price = 0.0
        self.total_invested = 0.0
        self.tp_price = None
        self.total_commission = 0.0
        self._last_close_pnl = None

    def auto_setup(self, symbol, leverage):
        return {"mode": "One-Way (ДЕМО)", "leverage": f"{leverage}x (ДЕМО)", "ok": True}

    def get_price(self, symbol):
        try:
            r = self.client.get_tickers(category=CATEGORY, symbol=symbol)
            return float(r["result"]["list"][0]["lastPrice"])
        except:
            return None

    def get_wallet_balance(self):
        return self.get_balance_info()

    def get_balance_info(self):
        pnl = round(self.balance - self.initial_balance, 2)
        return {
            "balance": round(self.balance, 2),
            "available": round(self.balance - self.total_invested, 2),
            "initial": self.initial_balance,
            "pnl": pnl,
            "pnl_pct": round(pnl / self.initial_balance * 100, 2),
            "commission": round(self.total_commission, 2)
        }

    def get_position_size(self, symbol):
        return self.position_size

    def place_market_buy(self, symbol, qty):
        price = self.get_price(symbol)
        if not price:
            return None
        margin = (qty * price) / LEVERAGE
        commission = qty * price * COMMISSION_PCT / 100
        total_cost = margin + commission
        if total_cost > self.balance:
            return None
        if self.position_size > 0:
            old_val = self.position_size * self.avg_entry_price
            self.avg_entry_price = (old_val + qty * price) / (self.position_size + qty)
        else:
            self.avg_entry_price = price
        self.position_size += qty
        self.total_invested += margin
        self.balance -= total_cost
        self.total_commission += commission
        return {"orderId": "demo", "avg_price": price, "qty": qty}

    def set_take_profit(self, symbol, tp_price):
        self.tp_price = tp_price
        return True

    def set_stop_loss_backup(self, symbol, sl_price):
        return True

    def clear_tp_sl(self, symbol):
        self.tp_price = None

    def cancel_all_orders(self, symbol):
        self.tp_price = None

    def market_close_all(self, symbol, qty):
        price = self.get_price(symbol)
        if not price:
            return False
        gross_pnl = self.position_size * (price - self.avg_entry_price)
        commission = self.position_size * price * COMMISSION_PCT / 100
        net_pnl = gross_pnl - commission
        self.balance += self.total_invested + net_pnl
        self._last_close_pnl = {"pnl": round(net_pnl, 2), "exit_price": price}
        self.position_size = 0
        self.total_invested = 0
        self.avg_entry_price = 0
        return True

    def get_closed_pnl(self, symbol):
        p = self._last_close_pnl
        self._last_close_pnl = None
        return p

    def get_available_margin(self):
        return self.balance - self.total_invested