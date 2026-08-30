"""
Исполнитель ордеров v2.

Три режима (EXECUTOR_MODE в .env):
  * paper   — виртуальная сделка пишется в журнал (data/v2_orders.jsonl) и
              сопровождается по стопам/целям. Это режим по умолчанию: бот
              остаётся советником, но у пользователя появляется проверяемая
              статистика «а сработали бы эти уровни»;
  * dry_run — всё считается и валидируется, но ничего не пишется и не шлётся;
  * live    — реальная отправка ордера на Bybit. Включается только двумя
              флагами сразу (EXECUTOR_MODE=live И EXECUTOR_ALLOW_LIVE=true)
              и только при наличии ключей: случайная торговля на живом счёте
              должна быть невозможна.

Порядок обязателен: претрейд-валидация → расчёт объёма → отправка → запись в
журнал. Риск-менеджер имеет право вето (RiskRejected) до любого обращения к
бирже — как в nautilus_trader, где лимиты проверяются до ордера.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from v2.config import V2Config
from v2.core.errors import ConfigError, RiskRejected
from v2.core.logging import get_logger
from v2.core.monitor import health, monitor
from v2.models import CoinReport

logger = get_logger("executor")


@dataclass
class OrderReceipt:
    symbol: str
    side: str
    qty: float
    entry: float
    stop_loss: float
    targets: list[float] = field(default_factory=list)
    notional_usd: float = 0.0
    risk_usd: float = 0.0
    leverage: int = 1
    mode: str = "paper"
    status: str = "filled"
    order_id: str = ""
    opened_ms: int = 0
    closed_ms: int = 0
    exit_price: float = 0.0
    pnl_usd: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class Executor:
    """Претрейд-валидация + исполнение (paper/dry_run/live)."""

    def __init__(self, config: V2Config, http=None) -> None:
        self.config = config
        self.http = http
        self.journal_path = Path(config.EXECUTOR_JOURNAL_PATH)
        self.orders: list[OrderReceipt] = []

    # ── претрейд-валидация ───────────────────────────────────────
    def validate(self, report: CoinReport) -> list[str]:
        """Возвращает список нарушений. Пустой список = ордер разрешён."""
        issues: list[str] = []
        plan = report.plan
        if report.verdict == "AVOID":
            issues.append("вердикт «Не входить»")
        if report.security.blocked:
            issues.append("токен заблокирован скам-фильтром")
        if plan.direction == "WAIT":
            issues.append("нет направления сделки")
        if plan.rr < self.config.MIN_RISK_REWARD:
            issues.append(f"R:R 1:{plan.rr:.1f} ниже минимума 1:{self.config.MIN_RISK_REWARD:.1f}")
        if plan.qty <= 0 or plan.position_usd <= 0:
            issues.append("нулевой объём позиции")
        if plan.position_pct > self.config.MAX_POSITION_PCT * 1.01:
            issues.append(f"позиция {plan.position_pct:.1f}% больше лимита {self.config.MAX_POSITION_PCT:.0f}%")
        if report.micro.slippage_pct > 2.0:
            issues.append(f"проскальзывание {report.micro.slippage_pct:.2f}% больше 2%")
        if report.risk_score > self.config.MAX_RISK_SCORE_TO_ENTER + 2:
            issues.append(f"риск {report.risk_score}/10 критический")
        return issues

    # ── открытие позиции ─────────────────────────────────────────
    async def open_position(self, report: CoinReport) -> OrderReceipt:
        issues = self.validate(report)
        if issues:
            raise RiskRejected("ордер отклонён: " + "; ".join(issues))

        plan = report.plan
        side = "buy" if plan.direction == "LONG" else "sell"
        receipt = OrderReceipt(
            symbol=report.token.cex_symbol or report.token.symbol,
            side=side,
            qty=plan.qty,
            entry=plan.entry,
            stop_loss=plan.stop_loss,
            targets=list(plan.targets),
            notional_usd=plan.position_usd,
            risk_usd=plan.risk_usd,
            leverage=plan.leverage,
            mode=self.config.EXECUTOR_MODE,
            opened_ms=int(time.time() * 1000),
        )

        if self.config.EXECUTOR_MODE == "dry_run":
            receipt.status = "simulated"
            logger.info("DRY-RUN: %s %s %.6f @ %.8g", side, receipt.symbol, receipt.qty, receipt.entry)
            return receipt

        if self.config.EXECUTOR_MODE == "live":
            order_id = await self._submit_live(receipt)
            receipt.order_id = order_id
            receipt.status = "submitted"

        # paper и live одинаково попадают в журнал: позиция сопровождается по уровням
        self.orders.append(receipt)
        self._append_journal({"event": "open", **receipt.to_dict()})
        health.mark("executor.open_positions", len([o for o in self.orders if not o.closed_ms]))
        logger.info(
            "%s: %s %s %.6f @ %.8g (стоп %.8g, цели %s)",
            self.config.EXECUTOR_MODE.upper(), side, receipt.symbol, receipt.qty, receipt.entry,
            receipt.stop_loss, receipt.targets,
        )
        return receipt

    # ── сопровождение: стоп / цель / трейлинг ────────────────────
    async def update(self, symbol: str, price: float) -> list[OrderReceipt]:
        """Проверяет открытые позиции по текущей цене. Возвращает закрытые."""
        closed: list[OrderReceipt] = []
        for order in self.orders:
            if order.closed_ms or order.symbol != symbol:
                continue
            exit_price = 0.0
            reason = ""
            if order.side == "buy":
                if price <= order.stop_loss:
                    exit_price, reason = order.stop_loss, "стоп-лосс"
                elif order.targets and price >= order.targets[0]:
                    exit_price, reason = order.targets[0], "цель 1"
            else:
                if price >= order.stop_loss:
                    exit_price, reason = order.stop_loss, "стоп-лосс"
                elif order.targets and price <= order.targets[0]:
                    exit_price, reason = order.targets[0], "цель 1"
            if not exit_price:
                continue
            direction = 1 if order.side == "buy" else -1
            order.pnl_usd = round((exit_price - order.entry) * order.qty * direction, 4)
            order.exit_price = exit_price
            order.closed_ms = int(time.time() * 1000)
            order.reason = reason
            order.status = "closed"
            closed.append(order)
            self._append_journal({"event": "close", **order.to_dict()})
            logger.info("Закрыта %s по %s: PnL $%.2f", symbol, reason, order.pnl_usd)
        if closed:
            health.mark("executor.open_positions", len([o for o in self.orders if not o.closed_ms]))
        return closed

    def stats(self) -> dict[str, Any]:
        finished = [o for o in self.orders if o.closed_ms]
        wins = [o for o in finished if o.pnl_usd > 0]
        pnl = sum(o.pnl_usd for o in finished)
        return {
            "mode": self.config.EXECUTOR_MODE,
            "opened": len(self.orders),
            "closed": len(finished),
            "win_rate": round(len(wins) / len(finished) * 100, 1) if finished else 0.0,
            "pnl_usd": round(pnl, 2),
            "open_positions": len(self.orders) - len(finished),
        }

    # ── журнал ───────────────────────────────────────────────────
    def _append_journal(self, record: dict[str, Any]) -> None:
        if self.config.EXECUTOR_MODE == "dry_run":
            return
        try:
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            with self.journal_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            monitor.record("executor.journal", exc)

    # ── живая отправка (Bybit V5) ────────────────────────────────
    async def _submit_live(self, receipt: OrderReceipt) -> str:
        if not self.config.EXECUTOR_ALLOW_LIVE:
            raise ConfigError(
                "EXECUTOR_MODE=live, но EXECUTOR_ALLOW_LIVE не включён — живая торговля запрещена"
            )
        if not (self.config.BYBIT_API_KEY and self.config.BYBIT_API_SECRET) or self.http is None:
            raise ConfigError("для live-режима нужны BYBIT_API_KEY/BYBIT_API_SECRET")

        base = "https://api-testnet.bybit.com" if self.config.BYBIT_TESTNET else "https://api.bybit.com"
        timestamp = str(int(time.time() * 1000))
        recv_window = "5000"
        body = json.dumps(
            {
                "category": "linear",
                "symbol": receipt.symbol,
                "side": "Buy" if receipt.side == "buy" else "Sell",
                "orderType": "Market",
                "qty": f"{receipt.qty:.6f}",
                "timeInForce": "GTC",
                "orderLinkId": f"v2{timestamp}",
            },
            separators=(",", ":"),
        )
        payload = timestamp + self.config.BYBIT_API_KEY + recv_window + body
        signature = hmac.new(
            self.config.BYBIT_API_SECRET.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        response = await self.http.post_json(
            f"{base}/v5/order/create",
            json_body=json.loads(body),
            headers={
                "X-BAPI-API-KEY": self.config.BYBIT_API_KEY,
                "X-BAPI-SIGN": signature,
                "X-BAPI-TIMESTAMP": timestamp,
                "X-BAPI-RECV-WINDOW": recv_window,
                "Content-Type": "application/json",
            },
            component="executor.live",
        )
        order_id = str(((response or {}).get("result") or {}).get("orderId") or "")
        if not order_id:
            monitor.record("executor.live", f"нет orderId в ответе: {response}")
        return order_id
