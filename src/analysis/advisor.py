"""
Советник по сделке: расчёт объёма позиции + пошаговая инструкция для новичка.

Risk-движок построен по образцу nautilus_trader (жёсткие лимиты до ордера) и
Jesse (размер позиции от процента риска). Порядок расчёта:

  риск в $  = депозит × риск%
  количество = риск$ / |вход − стоп|
  объём в $ = количество × вход
  маржа     = объём / плечо

Плечо НЕ увеличивает риск (он задан процентом), оно лишь уменьшает
замороженную маржу. Если маржа не влезает в лимит — позиция урезается,
а фактический риск пересчитывается и показывается честно.

Вторая часть — «что куда нажимать»: пошаговые инструкции под конкретную
биржу (Bybit / Binance) и рынок (фьючерсы / спот) с подставленными числами.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from src.analysis.engine import AnalysisResult
from src.core.fmt import fmt_pct, fmt_price, fmt_qty, fmt_usd
from src.core.logging import get_logger
from src.data.models import Instrument, Ticker, normalize_symbol

logger = get_logger("analysis.advisor")

EXCHANGES = ("bybit", "binance")
MARKETS = ("futures", "spot")

# Ориентировочные комиссии (taker) — для оценки точки безубыточности
FEES = {
    ("bybit", "futures"): (0.0002, 0.00055),
    ("bybit", "spot"): (0.001, 0.001),
    ("binance", "futures"): (0.0002, 0.0005),
    ("binance", "spot"): (0.001, 0.001),
}


@dataclass
class RiskCheck:
    ok: bool
    issues: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class TradeCard:
    """Готовая карточка сделки для новичка."""

    symbol: str
    base: str
    side: str                       # LONG / SHORT / WAIT
    market: str                     # futures / spot
    exchange: str                   # bybit / binance
    order_type: str                 # Лимитный / Рыночный
    price_now: float
    entry_zone: tuple[float, float]
    entry_ref: float
    stop_loss: float
    targets: list[float]
    rr: float
    # деньги
    deposit_usd: float
    risk_pct: float
    risk_usd: float
    qty: float
    notional_usd: float
    leverage: int
    margin_usd: float
    liq_price_est: float | None
    stop_dist_pct: float
    t1_dist_pct: float
    fee_entry_usd: float
    fee_exit_usd: float
    breakeven_price: float
    profit_t1_usd: float
    profit_t2_usd: float
    loss_stop_usd: float
    # инструкция
    steps: list[str] = field(default_factory=list)
    checklist: list[str] = field(default_factory=list)
    exit_rules: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    scale_price: int = 4
    scale_qty: int = 4
    min_notional: float = 5.0
    risk_check: RiskCheck = field(default_factory=lambda: RiskCheck(ok=True))
    is_demo: bool = False

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "market": self.market,
            "exchange": self.exchange,
            "order_type": self.order_type,
            "price_now": self.price_now,
            "entry_zone": [self.entry_zone[0], self.entry_zone[1]],
            "entry_ref": self.entry_ref,
            "stop_loss": self.stop_loss,
            "targets": self.targets,
            "rr": round(self.rr, 2),
            "deposit_usd": self.deposit_usd,
            "risk_pct": self.risk_pct,
            "risk_usd": round(self.risk_usd, 2),
            "qty": self.qty,
            "notional_usd": round(self.notional_usd, 2),
            "leverage": self.leverage,
            "margin_usd": round(self.margin_usd, 2),
            "liq_price_est": self.liq_price_est,
            "stop_dist_pct": round(self.stop_dist_pct, 2),
            "t1_dist_pct": round(self.t1_dist_pct, 2),
            "profit_t1_usd": round(self.profit_t1_usd, 2),
            "profit_t2_usd": round(self.profit_t2_usd, 2),
            "loss_stop_usd": round(self.loss_stop_usd, 2),
            "breakeven_price": self.breakeven_price,
            "steps": self.steps,
            "checklist": self.checklist,
            "exit_rules": self.exit_rules,
            "warnings": self.warnings,
            "ok": self.risk_check.ok,
            "issues": self.risk_check.issues,
            "is_demo": self.is_demo,
        }

    def header(self) -> str:
        icon = {"LONG": "🟢", "SHORT": "🔴"}.get(self.side, "⏸")
        kind = "фьючерсы" if self.market == "futures" else "спот"
        return (
            f"{icon} <b>{self.symbol}</b> — {self.side} ({kind}, {self.exchange})\n"
            f"Рейтинг сделки: R:R <b>1:{self.rr:.1f}</b> | риск {self.risk_pct:g}% депозита"
        )

    def money_block(self) -> str:
        lines = [
            "💰 <b>СКОЛЬКО ПОКУПАТЬ</b>",
            f"Депозит: <b>{fmt_usd(self.deposit_usd)}</b>",
            f"Риск на сделку ({self.risk_pct:g}%): <b>{fmt_usd(self.risk_usd)}</b>",
            f"Объём позиции: <b>{fmt_usd(self.notional_usd)}</b>",
            f"Количество: <b>{fmt_qty(self.qty, self.scale_qty)} {self.base}</b>",
        ]
        if self.market == "futures":
            lines.append(f"Плечо: <b>{self.leverage}x</b> → маржа <b>{fmt_usd(self.margin_usd)}</b>")
            if self.liq_price_est:
                lines.append(f"Ликвидация (оценка): <b>{fmt_price(self.liq_price_est, self.scale_price)}</b>")
        lines.append(f"Стоп-лосс заберёт: <b>{fmt_usd(-abs(self.loss_stop_usd))}</b>")
        lines.append(f"Цель 1 принесёт: <b>{fmt_usd(self.profit_t1_usd)}</b>")
        if self.profit_t2_usd:
            lines.append(f"Цель 2 принесёт: <b>{fmt_usd(self.profit_t2_usd)}</b>")
        return "\n".join(lines)

    def levels_block(self) -> str:
        lo, hi = self.entry_zone
        lines = [
            "🎯 <b>УРОВНИ</b>",
            f"Вход (лимит): <b>{fmt_price(lo, self.scale_price)} – {fmt_price(hi, self.scale_price)}</b>",
            f"Стоп-лосс: <b>{fmt_price(self.stop_loss, self.scale_price)}</b> ({fmt_pct(-self.stop_dist_pct)})",
        ]
        for i, t in enumerate(self.targets[:3], 1):
            share = "50% позиции" if i == 1 else ("30% позиции" if i == 2 else "остаток")
            lines.append(f"Цель {i}: <b>{fmt_price(t, self.scale_price)}</b> — закрыть {share}")
        lines.append(f"Безубыток (с учётом комиссий): {fmt_price(self.breakeven_price, self.scale_price)}")
        return "\n".join(lines)

    def steps_block(self) -> str:
        return "👆 <b>ЧТО НАЖИМАТЬ</b>\n" + "\n".join(self.steps)

    def to_text(self) -> str:
        parts = [self.header(), "", self.levels_block(), "", self.money_block(), "", self.steps_block()]
        if self.checklist:
            parts.append("")
            parts.append("✅ <b>ПРОВЕРЬ ПЕРЕД ВХОДОМ</b>\n" + "\n".join(f"• {c}" for c in self.checklist))
        if self.exit_rules:
            parts.append("")
            parts.append("🚪 <b>КОГДА ВЫХОДИТЬ</b>\n" + "\n".join(f"• {r}" for r in self.exit_rules))
        if self.warnings:
            parts.append("")
            parts.append("⚠️ " + "\n⚠️ ".join(self.warnings))
        if self.risk_check.issues:
            parts.append("")
            parts.append("🚫 <b>СДЕЛКУ НЕ РЕКОМЕНДУЮ</b>\n" + "\n".join(f"• {i}" for i in self.risk_check.issues))
        if self.is_demo:
            parts.append("")
            parts.append("<i>⚠️ Демо-рынок: числа синтетические, это не сигнал к реальной сделке.</i>")
        return "\n".join(parts)


# ════════════════════════════════════════════════════════════════
#  RISK-ДВИЖОК
# ════════════════════════════════════════════════════════════════
class RiskEngine:
    """Жёсткие лимиты: плечо, доля депозита, минимальный R:R."""

    def __init__(self, settings):
        self.settings = settings

    def max_leverage(self, atr_pct: float, vol_state: str, instrument_max: int = 100) -> int:
        """Плечо обратно пропорционально волатильности: риск 2% на «плечо»."""
        base = 2.0 / max(atr_pct, 0.25)
        lev = int(math.floor(base))
        if vol_state in ("extreme", "high"):
            lev -= 2
        elif vol_state == "low":
            lev += 1
        lev = max(1, min(lev, self.settings.MAX_LEVERAGE, instrument_max))
        # новичкам выше 10x не даём даже если настройки позволяют
        return int(min(lev, 10))

    def size_position(
        self,
        deposit: float,
        risk_pct: float,
        entry: float,
        stop: float,
        leverage: int,
        instrument: Instrument | None = None,
    ) -> tuple[float, float, float, float, list[str]]:
        """
        Возвращает (qty, notional, margin, effective_risk_usd, notes).
        Соблюдает лимит доли депозита в марже и минимальный объём биржи.
        """
        notes: list[str] = []
        risk_usd = deposit * risk_pct / 100.0
        stop_dist = abs(entry - stop)
        if stop_dist <= 0 or entry <= 0:
            return 0.0, 0.0, 0.0, 0.0, ["Стоп совпадает со входом — риск не измерим"]

        qty = risk_usd / stop_dist
        notional = qty * entry
        margin = notional / max(leverage, 1)

        # лимит доли депозита в марже (MAX_POSITION_PCT)
        margin_cap = deposit * self.settings.MAX_POSITION_PCT / 100.0
        if margin > margin_cap:
            new_notional = margin_cap * leverage
            new_qty = new_notional / entry
            notes.append(
                f"Позиция урезана до лимита маржи {self.settings.MAX_POSITION_PCT:g}% депозита"
            )
            qty, notional, margin = new_qty, new_notional, margin_cap
            risk_usd = qty * stop_dist

        # минимальный объём ордера биржи
        min_notional = instrument.min_notional if instrument else 5.0
        if notional < min_notional:
            notes.append(
                f"Объём меньше минимума биржи {fmt_usd(min_notional)} — увеличь депозит или риск"
            )

        # точность биржи
        if instrument is not None:
            qty = instrument.round_qty(qty)
            notional = qty * entry
            margin = notional / max(leverage, 1)
            risk_usd = qty * stop_dist

        return qty, notional, margin, risk_usd, notes

    def validate(self, result: AnalysisResult, card: TradeCard) -> RiskCheck:
        issues: list[str] = []
        notes: list[str] = []
        plan = result.plan
        if plan is None:
            issues.append("Сейчас нет сценария входа — бот советует подождать (WAIT)")
            return RiskCheck(ok=False, issues=issues, notes=notes)
        if card.rr < self.settings.MIN_RISK_REWARD:
            issues.append(
                f"R:R 1:{card.rr:.1f} ниже минимума {self.settings.MIN_RISK_REWARD:g} — "
                "математическое ожидание против тебя"
            )
        if result.confidence < 0.45:
            issues.append(f"Уверенность сценария всего {result.confidence*100:.0f}% — сигнал слабый")
        if result.volatility.state == "extreme":
            notes.append("Волатильность экстремальная: стоп может сработать на шуме")
        if card.qty <= 0:
            issues.append("Расчёт дал нулевой объём — проверь депозит и риск")
        if result.direction == "SHORT" and card.market == "spot":
            issues.append("На споте нельзя открыть SHORT — шорт доступен только на фьючерсах")
        return RiskCheck(ok=not issues, issues=issues, notes=notes)


# ════════════════════════════════════════════════════════════════
#  ПОШАГОВЫЕ ИНСТРУКЦИИ
# ════════════════════════════════════════════════════════════════
def _bybit_futures_steps(c: TradeCard) -> list[str]:
    green = "Купить / Long" if c.side == "LONG" else "Продать / Short"
    lo, hi = c.entry_zone
    return [
        "1. Открой приложение <b>Bybit</b> → внизу вкладка <b>«Деривативы»</b> (Derivatives).",
        f"2. В строке поиска сверху введи <b>{c.base}</b> и выбери пару <b>{c.symbol}</b> (USDT Perpetual).",
        "3. Слева от плеча нажми на тип маржи и выбери <b>«Изолированная»</b> — тогда убыток "
        "ограничен маржой сделки, а не всем депозитом.",
        f"4. Нажми на цифру плеча (например «10x») → поставь ползунок на <b>{c.leverage}x</b> → «Подтвердить».",
        "5. В панели ордера выбери тип <b>«Лимитный»</b> (Limit).",
        f"6. В поле «Цена» вставь <b>{fmt_price(lo, c.scale_price)}</b> "
        f"(диапазон зоны входа {fmt_price(lo, c.scale_price)}–{fmt_price(hi, c.scale_price)}).",
        f"7. В поле «Количество» переключи на <b>USDT</b> и введи <b>{c.notional_usd:.2f}</b>. "
        f"Это даст ~{fmt_qty(c.qty, c.scale_qty)} {c.base}.",
        f"8. Нажми на шестерёнку/«TP/SL» в ордере и заполни: "
        f"<b>Take Profit {fmt_price(c.targets[0], c.scale_price)}</b>, "
        f"<b>Stop Loss {fmt_price(c.stop_loss, c.scale_price)}</b>.",
        f"9. Проверь строку «Маржа»: должно быть около <b>{fmt_usd(c.margin_usd)}</b>. "
        f"Если сильно больше — уменьши объём.",
        f"10. Нажми зелёную <b>«{green}»</b>. Ордер встанет в стакан и сработает, когда цена дойдёт до зоны.",
        "11. После исполнения зайди во вкладку <b>«Позиции»</b> и убедись, что TP и SL стоят. "
        "Если пусто — нажми «TP/SL» у позиции и впиши те же числа.",
    ]


def _bybit_spot_steps(c: TradeCard) -> list[str]:
    return [
        "1. Открой <b>Bybit</b> → внизу вкладка <b>«Спот»</b> (Trade → Spot).",
        f"2. В поиске найди <b>{c.base}</b> → открой пару <b>{c.base}/USDT</b>.",
        "3. Выбери тип ордера <b>«Лимитный»</b> (Limit).",
        f"4. Цена: <b>{fmt_price(c.entry_zone[0], c.scale_price)}</b>.",
        f"5. Количество: переключи на <b>USDT</b> и введи <b>{c.notional_usd:.2f}</b> "
        f"(~{fmt_qty(c.qty, c.scale_qty)} {c.base}).",
        f"6. Нажми <b>«Купить {c.base}»</b>.",
        f"7. Сразу после покупки поставь защиту: вкладка «Активы» → позиция {c.base} → "
        f"<b>«TP/SL»</b> → Take Profit <b>{fmt_price(c.targets[0], c.scale_price)}</b>, "
        f"Stop Loss <b>{fmt_price(c.stop_loss, c.scale_price)}</b>.",
        "8. На споте шорт недоступен и ликвидации нет — но стоп обязателен, иначе убыток не ограничен.",
    ]


def _binance_futures_steps(c: TradeCard) -> list[str]:
    green = "Купить/Long" if c.side == "LONG" else "Продать/Short"
    return [
        "1. Открой <b>Binance</b> → внизу <b>«Фьючерсы»</b> → <b>USDⓈ-M</b> → USDT-M фьючерсы.",
        f"2. В поиске введи <b>{c.base}</b> → выбери <b>{c.base}USDT Perpetual</b>.",
        "3. Нажми на «Кросс-маржа» сверху и выбери <b>«Изолированная»</b>.",
        f"4. Нажми на «20x» → ползунок на <b>{c.leverage}x</b> → «Подтвердить».",
        "5. Тип ордера: <b>Limit</b>.",
        f"6. Цена: <b>{fmt_price(c.entry_zone[0], c.scale_price)}</b>.",
        f"7. Размер: переключи на <b>USDT</b> и введи <b>{c.notional_usd:.2f}</b>.",
        f"8. Раскрой <b>«TP/SL»</b>: Take Profit <b>{fmt_price(c.targets[0], c.scale_price)}</b>, "
        f"Stop Loss <b>{fmt_price(c.stop_loss, c.scale_price)}</b>.",
        f"9. Сравни «Маржу» с расчётной <b>{fmt_usd(c.margin_usd)}</b>.",
        f"10. Нажми <b>«{green}»</b>.",
        "11. Вкладка «Позиции»: проверь TP/SL, при необходимости добавь через «TP/SL».",
    ]


def _binance_spot_steps(c: TradeCard) -> list[str]:
    return [
        "1. Открой <b>Binance</b> → <b>«Торговать»</b> → <b>«Спот»</b> (или «Advanced»).",
        f"2. Найди пару <b>{c.base}/USDT</b>.",
        "3. Тип ордера: <b>Limit</b>.",
        f"4. Цена: <b>{fmt_price(c.entry_zone[0], c.scale_price)}</b>.",
        f"5. Количество: переключи ползунок на USDT и введи <b>{c.notional_usd:.2f}</b>.",
        f"6. Нажми <b>«Купить {c.base}»</b>.",
        f"7. Защита: «Активы» → {c.base} → «Продать» → тип <b>OCO</b> → "
        f"Take Profit <b>{fmt_price(c.targets[0], c.scale_price)}</b>, "
        f"Stop <b>{fmt_price(c.stop_loss, c.scale_price)}</b>. OCO закроет позицию по первому сработавшему уровню.",
    ]


def _steps_for(c: TradeCard) -> list[str]:
    if c.exchange == "binance":
        return _binance_futures_steps(c) if c.market == "futures" else _binance_spot_steps(c)
    return _bybit_futures_steps(c) if c.market == "futures" else _bybit_spot_steps(c)


def _checklist(c: TradeCard, result: AnalysisResult) -> list[str]:
    items = [
        f"Риск на сделку не больше {c.risk_pct:g}% депозита ({fmt_usd(c.risk_usd)}).",
        f"Стоп-лосс {fmt_price(c.stop_loss, c.scale_price)} выставлен ВМЕСТЕ с ордером, а не «потом».",
        f"R:R минимум 1:{c.rr:.1f} — если цели ближе, сделку пропускаем.",
        "Маржа изолированная: потеряешь не больше, чем вложил в сделку.",
        "Нет важных новостей в ближайший час (CPI, ФРС, отчёты, листинги).",
        "Ты не увеличиваешь позицию после убытка и не усредняешь против стопа.",
    ]
    if result.volatility.state in ("extreme", "high"):
        items.append("Волатильность высокая — рассматривай риск 0.5% вместо 1%.")
    return items


def _exit_rules(c: TradeCard) -> list[str]:
    rules = [
        f"Цель 1 {fmt_price(c.targets[0], c.scale_price)}: закрой 50% и передвинь стоп в безубыток "
        f"({fmt_price(c.breakeven_price, c.scale_price)}).",
    ]
    if len(c.targets) > 1:
        rules.append(
            f"Цель 2 {fmt_price(c.targets[1], c.scale_price)}: закрой ещё 30%, остаток веди по тренду."
        )
    if len(c.targets) > 2:
        rules.append(f"Цель 3 {fmt_price(c.targets[2], c.scale_price)}: закрой остаток.")
    rules.append(
        f"Стоп {fmt_price(c.stop_loss, c.scale_price)} сработал — выходи полностью, без «подожду отскока»."
    )
    rules.append("Сценарий отменён, если цена закрепилась за стопом на закрытии 15-минутной свечи.")
    return rules


# ════════════════════════════════════════════════════════════════
#  СБОРКА КАРТОЧКИ
# ════════════════════════════════════════════════════════════════
class TradeAdvisor:
    """Строит TradeCard по результату анализа."""

    def __init__(self, source, settings):
        self.source = source
        self.settings = settings
        self.risk = RiskEngine(settings)

    async def build(
        self,
        result: AnalysisResult,
        deposit_usd: float | None = None,
        risk_pct: float | None = None,
        leverage: int | None = None,
        exchange: str = "bybit",
        market: str = "futures",
    ) -> TradeCard:
        plan = result.plan
        symbol = result.symbol
        base = normalize_symbol(symbol).replace("USDT", "")
        deposit = float(deposit_usd or self.settings.DEFAULT_DEPOSIT_USD)
        risk_pct = float(risk_pct if risk_pct is not None else self.settings.RISK_PER_TRADE_PCT)
        exchange = exchange if exchange in EXCHANGES else "bybit"
        market = market if market in MARKETS else "futures"

        instrument = None
        try:
            await self.source.discover_instruments()
            instrument = self.source.get_instrument(symbol)
        except Exception as e:  # noqa: BLE001
            logger.debug("Инструмент %s недоступен: %s", symbol, e)

        scale_price = instrument.price_scale if instrument else _guess_scale(result.price)
        scale_qty = instrument.qty_scale if instrument else 4
        min_notional = instrument.min_notional if instrument else 5.0
        inst_max_lev = instrument.max_leverage if instrument else 100

        if plan is None:
            return self._wait_card(
                symbol, base, result, deposit, risk_pct, exchange, market, scale_price, scale_qty
            )

        is_long = plan.direction == "LONG"
        entry_ref = plan.entry_zone[0] if is_long else plan.entry_zone[1]
        stop = plan.stop_loss
        targets = list(plan.targets)
        stop_dist_pct = abs(entry_ref - stop) / entry_ref * 100.0

        lev = int(leverage) if leverage else self.risk.max_leverage(
            result.volatility.atr_pct, result.volatility.state, inst_max_lev
        )
        if market == "spot":
            lev = 1
        lev = max(1, min(lev, self.settings.MAX_LEVERAGE))

        qty, notional, margin, risk_usd, notes = self.risk.size_position(
            deposit, risk_pct, entry_ref, stop, lev, instrument
        )

        maker, taker = FEES.get((exchange, market), (0.001, 0.001))
        fee_entry = notional * taker
        fee_exit = notional * taker
        profit_t1 = qty * abs(targets[0] - entry_ref) - fee_entry - fee_exit
        profit_t2 = (
            qty * abs(targets[1] - entry_ref) - fee_entry - fee_exit if len(targets) > 1 else 0.0
        )
        loss_stop = qty * abs(entry_ref - stop) + fee_entry + fee_exit
        bev_shift = (fee_entry + fee_exit) / max(qty, 1e-12)
        breakeven = entry_ref + bev_shift if is_long else max(entry_ref - bev_shift, 0.0)

        liq = None
        if market == "futures" and lev > 1:
            # грубая оценка: вход × (1 ∓ 1/плечо) с поправкой на ставку поддержки 0.5%
            mmr = 0.005
            liq = entry_ref * (1 - (1 / lev - mmr)) if is_long else entry_ref * (1 + (1 / lev - mmr))

        card = TradeCard(
            symbol=symbol,
            base=base,
            side=plan.direction,
            market=market,
            exchange=exchange,
            order_type="Лимитный (Limit)",
            price_now=result.price,
            entry_zone=(plan.entry_zone[0], plan.entry_zone[1]),
            entry_ref=entry_ref,
            stop_loss=stop,
            targets=targets,
            rr=plan.rr,
            deposit_usd=deposit,
            risk_pct=risk_pct,
            risk_usd=risk_usd,
            qty=qty,
            notional_usd=notional,
            leverage=lev,
            margin_usd=margin if market == "futures" else notional,
            liq_price_est=liq,
            stop_dist_pct=stop_dist_pct,
            t1_dist_pct=plan.t1_distance_pct,
            fee_entry_usd=fee_entry,
            fee_exit_usd=fee_exit,
            breakeven_price=breakeven,
            profit_t1_usd=profit_t1,
            profit_t2_usd=profit_t2,
            loss_stop_usd=loss_stop,
            scale_price=scale_price,
            scale_qty=scale_qty,
            min_notional=min_notional,
            is_demo=result.is_demo,
        )
        card.steps = _steps_for(card)
        card.checklist = _checklist(card, result)
        card.exit_rules = _exit_rules(card)
        card.warnings = list(notes)
        if result.volatility.state == "extreme":
            card.warnings.append("Экстремальная волатильность: держи объём вдвое меньше обычного")
        if plan.distance_pct > 3.0:
            card.warnings.append(
                f"До зоны входа {plan.distance_pct:.1f}% — не покупай по рынку, жди лимитник"
            )
        card.risk_check = self.risk.validate(result, card)
        return card

    def _wait_card(
        self, symbol, base, result, deposit, risk_pct, exchange, market, scale_price, scale_qty
    ) -> TradeCard:
        """Карточка для WAIT: входа нет, объясняем почему и что делать."""
        card = TradeCard(
            symbol=symbol,
            base=base,
            side="WAIT",
            market=market,
            exchange=exchange,
            order_type="—",
            price_now=result.price,
            entry_zone=(result.price, result.price),
            entry_ref=result.price,
            stop_loss=0.0,
            targets=[],
            rr=0.0,
            deposit_usd=deposit,
            risk_pct=risk_pct,
            risk_usd=0.0,
            qty=0.0,
            notional_usd=0.0,
            leverage=1,
            margin_usd=0.0,
            liq_price_est=None,
            stop_dist_pct=0.0,
            t1_dist_pct=0.0,
            fee_entry_usd=0.0,
            fee_exit_usd=0.0,
            breakeven_price=result.price,
            profit_t1_usd=0.0,
            profit_t2_usd=0.0,
            loss_stop_usd=0.0,
            scale_price=scale_price,
            scale_qty=scale_qty,
            is_demo=result.is_demo,
        )
        card.steps = [
            "1. Сейчас бот не видит подтверждённого сценария — правильная позиция: <b>не входить</b>.",
            "2. Добавь монету в наблюдение (кнопка «➕ В наблюдение»), чтобы получать алерты.",
            "3. Жди одного из двух: цена вернулась в зону коррекции ИЛИ старший таймфрейм сменил тренд.",
            "4. Нажми «🔬 Полный анализ» позже — если появится LONG/SHORT, карточка посчитает объём и уровни.",
        ]
        card.checklist = [
            "Нет сигнала = нет сделки. Пропуск сделки не убыток.",
            "Держи депозит свободным: лучшие сетапы приходят 2–3 раза в неделю, не каждый час.",
        ]
        card.exit_rules = ["Позиции нет — выходить не из чего."]
        card.warnings = [
            f"Уверенность сценария {result.confidence*100:.0f}%, направление {result.direction}",
            plan_invalidation_hint(result),
        ]
        card.risk_check = RiskCheck(ok=False, issues=["Нет подтверждённого сценария входа (WAIT)"])
        return card


def plan_invalidation_hint(result: AnalysisResult) -> str:
    if result.plan and result.plan.invalidation:
        return f"Сценарий отменится, если: {result.plan.invalidation}"
    return "Сценарий отменится при смене тренда на 1h/4h"


def _guess_scale(price: float) -> int:
    p = abs(price)
    if p >= 1000:
        return 2
    if p >= 1:
        return 4
    if p >= 0.01:
        return 5
    return 8


# ════════════════════════════════════════════════════════════════
#  ГАЙД ДЛЯ НОВИЧКА
# ════════════════════════════════════════════════════════════════
BEGINNER_GUIDE = """📚 <b>ГАЙД: как пользоваться советником</b>

<b>1. Что делает бот</b>
Он НЕ торгует за тебя и не имеет доступа к твоему счёту. Он собирает данные с биржи,
считает 60+ индикаторов на 5 таймфреймах и выдаёт готовый план: куда входить,
где стоп, где цели и сколько купить.

<b>2. Три главные кнопки</b>
🔬 <b>Полный анализ</b> — спектр по всем таймфреймам и факторам + вердикт.
🎯 <b>План входа</b> — карточка сделки: уровни, объём в USDT и монетах, плечо,
и пошаговая инструкция «что нажимать» в приложении биржи.
📉 <b>График</b> — свечи с зоной входа, стопом и целями.

<b>3. Как читать вердикт</b>
🟢 LONG — ждём рост. 🔴 SHORT — ждём падение (только фьючерсы).
⏸ WAIT — сценария нет, правильная позиция не входить.
R:R 1:3 означает: рискуешь $10, чтобы заработать $30.

<b>4. Правило риска (главное правило)</b>
Рискуй 1% депозита на сделку. При депозите $500 это $5.
Бот считает объём позиции так, чтобы при срабатывании стопа ты потерял именно эти $5.
Стоп-лосс ставится ВМЕСТЕ с ордером, а не «после исполнения».

<b>5. Плечо</b>
Плечо не увеличивает твой риск — риск задан процентом. Плечо лишь уменьшает
замороженную маржу. Новичку: не выше 3–5x. Ставь <b>изолированную</b> маржу,
тогда потеря ограничена суммой сделки, а не всем депозитом.

<b>6. Выход из сделки</b>
На цели 1 закрой 50% позиции и перенеси стоп в безубыток. Дальше сделка
не может стать убыточной. Остаток веди по целям 2 и 3.

<b>7. Чего не делать</b>
• не усредняй против стопа и не «пересиживай» убыток
• не увеличивай объём после убытка (это мартингейл — он сливает депозит)
• не торгуй на всю котлету и не входи по рынку, когда бот дал лимитную зону
• не отменяй стоп «на минутку»

⚠️ Это аналитика, а не гарантия прибыли. Криптовалюты могут обесцениться полностью."""
