"""
Real-Time Paper Trading Broker Simulator.
"""
import uuid
from typing import Any

from src.config.settings import settings
from src.core.logging import get_logger
from src.core.time_utils import utc_now_ms
from src.paper.portfolio import PaperPositionState, VirtualPortfolio
from src.signals.models import SignalSetup

logger = get_logger("paper.engine")


class PaperTradingEngine:
    """
    Virtual perpetual futures exchange and execution simulator.
    """

    def __init__(self, initial_balance: float = 10000.0):
        self.portfolio = VirtualPortfolio(initial_balance=initial_balance, cash_balance=initial_balance)
        self.closed_trades: list[dict[str, Any]] = []

    def open_position_from_signal(self, signal: SignalSetup, allocated_margin: float) -> PaperPositionState | None:
        if signal.direction.value not in ("LONG", "SHORT"):
            return None

        if allocated_margin > self.portfolio.available_balance:
            logger.warning("Paper trade rejected: insufficient available margin", requested=allocated_margin, available=self.portfolio.available_balance)
            return None

        symbol = signal.symbol
        if symbol in self.portfolio.open_positions:
            logger.warning("Paper position already open for symbol", symbol=symbol)
            return None

        lev = signal.recommended_leverage
        entry_p = signal.entry_price
        notional = allocated_margin * lev
        qty = notional / entry_p
        entry_fee = notional * (settings.TAKER_FEE_PERCENT / 100.0)

        # Deduct entry fee
        self.portfolio.cash_balance -= entry_fee
        self.portfolio.total_commission_paid += entry_fee

        pos_id = f"POS-{symbol}-{uuid.uuid4().hex[:6]}"
        pos = PaperPositionState(
            position_id=pos_id,
            symbol=symbol,
            side=signal.direction.value,
            quantity=round(qty, 4),
            entry_price=entry_p,
            current_price=entry_p,
            leverage=lev,
            margin_locked=round(allocated_margin, 2),
            stop_loss=signal.stop_loss,
            take_profit_1=signal.take_profit_1,
            take_profit_2=signal.take_profit_2,
            take_profit_3=signal.take_profit_3,
            commission_paid=round(entry_fee, 4),
            opened_at_ms=utc_now_ms(),
        )

        self.portfolio.open_positions[symbol] = pos
        logger.info("Paper position opened", symbol=symbol, side=pos.side, qty=pos.quantity, entry=pos.entry_price)
        return pos

    def update_price_and_check_triggers(self, symbol: str, current_price: float) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        if symbol not in self.portfolio.open_positions:
            return events

        pos = self.portfolio.open_positions[symbol]
        pos.current_price = current_price

        # Calculate unrealized PnL
        if pos.side == "LONG":
            pos.unrealized_pnl = round(pos.quantity * (current_price - pos.entry_price), 2)
            # Check Stop Loss
            if current_price <= pos.stop_loss:
                closed_trade = self._close_position(symbol, current_price, reason="STOP_LOSS")
                events.append(closed_trade)
            # Check TP1
            elif current_price >= pos.take_profit_1:
                closed_trade = self._close_position(symbol, current_price, reason="TAKE_PROFIT_1")
                events.append(closed_trade)

        elif pos.side == "SHORT":
            pos.unrealized_pnl = round(pos.quantity * (pos.entry_price - current_price), 2)
            # Check Stop Loss
            if current_price >= pos.stop_loss:
                closed_trade = self._close_position(symbol, current_price, reason="STOP_LOSS")
                events.append(closed_trade)
            # Check TP1
            elif current_price <= pos.take_profit_1:
                closed_trade = self._close_position(symbol, current_price, reason="TAKE_PROFIT_1")
                events.append(closed_trade)

        return events

    def _close_position(self, symbol: str, exit_price: float, reason: str = "MANUAL") -> dict[str, Any]:
        pos = self.portfolio.open_positions.pop(symbol)
        exit_fee = pos.quantity * exit_price * (settings.TAKER_FEE_PERCENT / 100.0)

        if pos.side == "LONG":
            gross_pnl = pos.quantity * (exit_price - pos.entry_price)
        else:
            gross_pnl = pos.quantity * (pos.entry_price - exit_price)

        net_pnl = gross_pnl - exit_fee
        self.portfolio.cash_balance += net_pnl
        self.portfolio.total_commission_paid += exit_fee
        self.portfolio.closed_trades_count += 1

        if net_pnl > 0:
            self.portfolio.winning_trades_count += 1
        else:
            self.portfolio.losing_trades_count += 1

        trade_record = {
            "position_id": pos.position_id,
            "symbol": symbol,
            "side": pos.side,
            "quantity": pos.quantity,
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "gross_pnl": round(gross_pnl, 2),
            "fee": round(exit_fee, 4),
            "net_pnl": round(net_pnl, 2),
            "reason": reason,
            "opened_at_ms": pos.opened_at_ms,
            "closed_at_ms": utc_now_ms(),
        }

        self.closed_trades.append(trade_record)
        logger.info("Paper position closed", symbol=symbol, pnl=trade_record["net_pnl"], reason=reason)
        return trade_record
