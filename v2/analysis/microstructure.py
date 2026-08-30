"""
Микроструктура рынка: стакан, стены, проскальзывание.

Отвечает на практический вопрос трейдера: «сколько я реально потеряю на входе
объёмом $5 000?» Для этого ордер «проливается» по уровням стакана, а не
считается по средней цене — разница и есть проскальзывание.

Для DEX-пулов стакан эмулируется из глубины ликвидности провайдером данных
(v2/data/demo.py, v2/data/dex.py), поэтому модуль работает с единым типом
OrderBookSnapshot независимо от источника.
"""

from __future__ import annotations

from v2.core.logging import get_logger
from v2.models import MicrostructureReport, OrderBookLevel, OrderBookSnapshot

logger = get_logger("analysis.micro")


def walk_book(levels: list[OrderBookLevel], notional_usd: float) -> tuple[float, float]:
    """
    Исполняет рыночный ордер на notional_usd по уровням стакана.

    Возвращает (средняя цена исполнения, исполнено в USD). Если глубины не
    хватает — вторая величина меньше запрошенной, и это само по себе сигнал
    «пул пустой».
    """
    filled_usd = 0.0
    filled_qty = 0.0
    remaining = notional_usd
    for level in levels:
        if remaining <= 0:
            break
        level_usd = level.price * level.qty
        take_usd = min(level_usd, remaining)
        take_qty = take_usd / level.price if level.price > 0 else 0.0
        filled_usd += take_usd
        filled_qty += take_qty
        remaining -= take_usd
    avg_price = filled_usd / filled_qty if filled_qty > 0 else 0.0
    return avg_price, filled_usd


def biggest_wall(levels: list[OrderBookLevel]) -> tuple[float, float]:
    """Крупнейшая стена: (объём в USD, цена уровня)."""
    if not levels:
        return 0.0, 0.0
    best = max(levels, key=lambda lv: lv.price * lv.qty)
    return best.price * best.qty, best.price


def depth_within(levels: list[OrderBookLevel], mid: float, pct: float, side: str) -> float:
    """Суммарный объём (USD) в пределах ±pct% от mid."""
    if mid <= 0:
        return 0.0
    if side == "bid":
        return sum(lv.price * lv.qty for lv in levels if lv.price >= mid * (1 - pct / 100))
    return sum(lv.price * lv.qty for lv in levels if lv.price <= mid * (1 + pct / 100))


def analyze_orderbook(book: OrderBookSnapshot, entry_size_usd: float) -> MicrostructureReport:
    """Полный разбор стакана под заданный объём входа."""
    report = MicrostructureReport(entry_size_usd=entry_size_usd, is_stub=book.is_stub)
    mid = book.mid
    report.mid_price = mid
    if mid <= 0:
        report.grade = "empty"
        report.notes.append("Стакан пуст — оценить проскальзывание невозможно, вход только мелким объёмом")
        return report

    report.spread_pct = book.spread_pct
    report.bid_depth_1pct_usd = depth_within(book.bids, mid, 1.0, "bid")
    report.ask_depth_1pct_usd = depth_within(book.asks, mid, 1.0, "ask")

    total_depth = report.bid_depth_1pct_usd + report.ask_depth_1pct_usd
    report.imbalance = (
        (report.bid_depth_1pct_usd - report.ask_depth_1pct_usd) / total_depth if total_depth > 0 else 0.0
    )

    report.biggest_bid_wall_usd, report.biggest_bid_wall_price = biggest_wall(book.bids)
    report.biggest_ask_wall_usd, report.biggest_ask_wall_price = biggest_wall(book.asks)

    # Вход в лонг = покупка по аскам; проскальзывание считаем от mid.
    fill_price, filled_usd = walk_book(book.asks, entry_size_usd)
    report.est_fill_price = fill_price
    if fill_price > 0:
        report.slippage_pct = (fill_price - mid) / mid * 100.0
        report.slippage_cost_usd = entry_size_usd * report.slippage_pct / 100.0
    if filled_usd < entry_size_usd * 0.999:
        report.notes.append(
            f"Глубины стакана не хватает: из ${entry_size_usd:,.0f} исполняется только ${filled_usd:,.0f}"
        )
        report.grade = "empty"
        return report

    # Оценка качества ликвидности
    if report.slippage_pct <= 0.15 and total_depth >= 10 * entry_size_usd:
        report.grade = "excellent"
    elif report.slippage_pct <= 0.6:
        report.grade = "ok"
    elif report.slippage_pct <= 2.0:
        report.grade = "thin"
    else:
        report.grade = "empty"

    # Здесь — только те пояснения, которых нет в фиксированных строках отчёта
    if report.spread_pct > 1.0:
        report.notes.append(f"Спред {report.spread_pct:.2f}% — широкий, используйте лимитный ордер")
    if report.imbalance > 0.3:
        report.notes.append("Перевес бидов: в стакане больше покупателей, давление вверх")
    elif report.imbalance < -0.3:
        report.notes.append("Перевес асков: в стакане больше продавцов, давление вниз")
    return report


def slippage_adjusted_entry(report: MicrostructureReport, side: str = "LONG") -> float:
    """
    Реалистичная цена входа для расчёта стопа/цели.

    Для лонга берём цену исполнения с проскальзыванием (а не mid), иначе R:R в
    отчёте будет красивее, чем в реальности.
    """
    if report.est_fill_price <= 0 or report.mid_price <= 0:
        return report.mid_price
    if side == "LONG":
        return max(report.est_fill_price, report.mid_price)
    return min(report.mid_price * 2 - report.est_fill_price, report.mid_price)
