"""Текстовый отчёт бэктеста — то, что приходит в Telegram по кнопке."""

from __future__ import annotations

from src.core.fmt import fmt_pct
from src.core.timeutil import fmt_dt_short

VERDICT_RU = {
    "есть эдж": "🟢 есть статистический эдж",
    "на грани": "🟡 на грани окупаемости",
    "убыточно": "🔴 убыточно после комиссий",
    "мало данных": "⚪️ слишком мало сделок для вывода",
    "нет сделок": "⚪️ за период не было ни одной сделки",
}


def backtest_report(result) -> str:
    m = result.metrics
    cfg = result.config
    lines: list[str] = []
    lines.append(f"🧪 БЭКТЕСТ {result.symbol} ({cfg.entry_tf})")
    lines.append(
        f"{fmt_dt_short(result.start_ts)} → {fmt_dt_short(result.end_ts)} · "
        f"{result.bars_analyzed} баров в анализе"
    )
    if result.is_demo:
        lines.append("⚠️ демо-данные — НЕ реальный рынок, только проверка механики")
    lines.append("")

    verdict = m.get("verdict", "")
    lines.append(VERDICT_RU.get(verdict, verdict))
    lines.append("")

    n = m.get("total_trades", 0)
    lines.append(f"Сделок: {n} из {m.get('signals_generated', 0)} сигналов")
    passed = m.get("signals_passed_filters", 0)
    if m.get("signal_directions"):
        lines.append("Сигналы: " + " · ".join(
            f"{k} {v}" for k, v in sorted(m["signal_directions"].items())))
    if passed:
        lines.append(
            f"Фильтры прошли {passed}, лимитник исполнился у {m.get('fill_rate_pct', 0)}%."
        )
        if m.get("fill_rate_pct", 100) < 40:
            lines.append(
                "⚠️ Зона входа часто стоит далеко от цены и не достигается. "
                "Вживую вы либо не войдёте, либо войдёте по рынку по другой цене — "
                "план на это не рассчитан."
            )
    if m.get("skip_reasons"):
        why = " · ".join(f"{k}: {v}" for k, v in
                         sorted(m["skip_reasons"].items(), key=lambda x: -x[1]))
        lines.append(f"Отклонено — {why}")
    if n == 0:
        lines.append("Ни один сигнал не прошёл фильтры (стоп, издержки, R:R, уверенность).")
        lines.append("")
        lines.append("Что смотреть: расширьте период, снизьте min_rr или шаг step.")
        return "\n".join(lines)

    lines.append(f"Винрейт: {m['win_rate']}%  ({m['wins']}✅ / {m['losses']}❌ / {m.get('breakevens', 0)}➖)")
    lines.append("")
    lines.append("─ ПО ДЕНЬГАМ (в R — кратностях риска) ─")
    lines.append(f"Итог: {m['total_r']:+.2f} R")
    lines.append(f"Матожидание на сделку: {m['expectancy_r']:+.3f} R")
    pf = m.get("profit_factor")
    lines.append(f"Profit factor: {pf if pf is not None else '∞'}")
    lines.append(f"Средний выигрыш {m['avg_win_r']:+.2f} R · средний проигрыш {m['avg_loss_r']:+.2f} R")
    lines.append(f"Макс. просадка: {m['max_drawdown_r']:.2f} R")
    lines.append(f"Макс. серия убытков: {m['max_consecutive_losses']} подряд")
    lines.append("")

    lines.append("─ ЧЕСТНАЯ ПРОВЕРКА ─")
    lines.append(
        f"Точка безубыточности при таком R:R — {m['breakeven_win_rate']}% винрейта. "
        f"Фактически {m['win_rate']}% ({m['edge_over_breakeven']:+.1f} п.п.)"
    )
    lines.append(f"Издержки съели: {m.get('fee_drag_r', 0):.2f} R суммарно "
                 f"({m.get('fee_drag_r', 0) / max(n, 1):.2f} R на сделку)")
    lines.append(f"Buy&hold за тот же период: {fmt_pct(m['buy_hold_pct'])}")
    lines.append("")

    if m.get("by_direction"):
        lines.append("─ ПО НАПРАВЛЕНИЮ ─")
        for d, v in m["by_direction"].items():
            arrow = "📈" if d == "LONG" else "📉"
            lines.append(
                f"{arrow} {d}: {v['trades']} сделок, винрейт {v['win_rate']}%, "
                f"итог {v['total_r']:+.2f} R"
            )
        lines.append("")

    if m.get("exit_reasons"):
        ru = {
            "target": "цель", "stop_loss": "стоп", "breakeven": "безубыток",
            "timeout": "тайм-аут", "gap_stop": "гэп сквозь стоп",
            "trailing": "трейлинг",
        }
        parts = [f"{ru.get(k, k)} {v}" for k, v in sorted(m["exit_reasons"].items(), key=lambda x: -x[1])]
        lines.append("Выходы: " + " · ".join(parts))
        lines.append(f"Средняя длительность: {m['avg_bars_held']} баров")
        lines.append(f"Медианный стоп: {m.get('median_stop_dist_pct', 0)}% от цены входа")
        if m.get("gap_stops"):
            g = m["gap_stops"]
            word = "сделка закрылась" if g == 1 else ("сделки закрылись" if g < 5 else "сделок закрылись")
            lines.append(f"⚠️ {g} {word} гэпом сквозь стоп — вход был уже за стопом, это убыток")
        lines.append("")

    lines.append(_advice(m, result))
    return "\n".join(lines)


def _advice(m: dict, result) -> str:
    """Что делать с этим результатом — без приукрашивания."""
    v = m.get("verdict")
    out: list[str] = ["─ ВЫВОД ─"]
    drag = m.get("fee_drag_r", 0) / max(m.get("total_trades", 1), 1)
    if v == "убыточно":
        out.append("Советы бота на этих данных в минус. Не торгуйте по ним.")
        if drag > 0.1:
            out.append(f"· издержки {drag:.2f}R на сделку — стоп слишком узкий, "
                       f"комиссия съедает риск; widen стоп или берите сделки реже")
        out.append("· что ещё проверить: фильтр по силе тренда, тайм-фрейм входа")
    elif v == "на грани":
        out.append("Эдж почти нулевой — на реальных спредах и слиппедже уйдёт в минус.")
        out.append("Торговать только как учебный разбор, не как сигнал.")
    elif v == "есть эдж":
        out.append("Плюсовое матожидание есть, но:")
        out.append(f"· серия из {m['max_consecutive_losses']} убытков подряд реальна — риск на сделку ≤1-2%")
        out.append(f"· просадка {m['max_drawdown_r']:.2f} R — держите депозит, который её переживёт")
    else:
        out.append("Сделок слишком мало: результат статистически не значим.")

    if result.is_demo:
        out.append("⚠️ Это демо-данные. Эдж по ним не существует — проверяйте на реальной истории.")
    out.append("⚠️ Бэктест ≠ будущая доходность. Бот не торгует — это советник.")
    return "\n".join(out)
