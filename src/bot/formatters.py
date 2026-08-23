"""
Telegram Message Formatters and Visual Visualizers.
"""
from typing import Any

from src.backtesting.metrics import BacktestMetrics
from src.paper.portfolio import VirtualPortfolio
from src.signals.models import SignalSetup


def render_progress_bar(val: float, max_val: float, length: int = 10) -> str:
    """Render text-based progress bar: ███████░░░."""
    ratio = min(1.0, max(0.0, val / max_val)) if max_val > 0 else 0.0
    filled = int(round(ratio * length))
    empty = length - filled
    return "█" * filled + "░" * empty


class BotFormatters:
    """
    Renders clean, structured markdown templates for Telegram.
    """

    @staticmethod
    def format_signal(setup: SignalSetup) -> str:
        dir_emoji = "🟢 LONG" if setup.direction.value == "LONG" else ("🔴 SHORT" if setup.direction.value == "SHORT" else "⚪ NO TRADE")
        sb = setup.score_breakdown

        lines = [
            "━━━━━━━━━━━━━━━━━━━━",
            f"🔥 **{setup.symbol} {dir_emoji}**",
            "━━━━━━━━━━━━━━━━━━━━",
            f"📊 **Счет:** `{setup.score:.0f}/100` ({setup.tier.value})",
            f"🎯 **Уверенность модели:** `{setup.confidence * 100:.0f}%`",
            f"⚡ **Реком. плечо:** `{setup.recommended_leverage}x`",
            f"🌐 **Режим рынка:** `{setup.market_regime}`",
            "",
            "📐 **ТОРГОВЫЙ ПЛАН:**",
            f"• Вход: `{setup.entry_zone}` ({setup.entry_type.value})",
            f"• Стоп-лосс: `${setup.stop_loss:,.2f}`",
            f"• Тейк 1: `${setup.take_profit_1:,.2f}`",
        ]
        if setup.take_profit_2:
            lines.append(f"• Тейк 2: `${setup.take_profit_2:,.2f}`")
        if setup.take_profit_3:
            lines.append(f"• Тейк 3: `${setup.take_profit_3:,.2f}`")

        lines.extend([
            f"• **Risk/Reward:** `1:{setup.risk_reward_ratio:.1f}`",
            "",
            "📊 **ДЕТАЛИЗАЦИЯ СКОРИНГА:**",
            f"Trend       {render_progress_bar(sb.trend, 15)} {sb.trend:.0f}/15",
            f"Structure   {render_progress_bar(sb.market_structure, 15)} {sb.market_structure:.0f}/15",
            f"Order Flow  {render_progress_bar(sb.order_flow, 15)} {sb.order_flow:.0f}/15",
            f"Volatility  {render_progress_bar(sb.volatility, 10)} {sb.volatility:.0f}/10",
            f"Open Int.   {render_progress_bar(sb.open_interest, 10)} {sb.open_interest:.0f}/10",
            f"Momentum    {render_progress_bar(sb.momentum, 10)} {sb.momentum:.0f}/10",
            "",
            "🔍 **ПОЧЕМУ ЭТОТ СЕТАП?**",
        ])

        for r in setup.primary_reasons[:4]:
            lines.append(f"✓ {r}")

        if setup.risk_factors:
            lines.append("\n⚠️ **РИСКИ И ИНВАЛИДАЦИЯ:**")
            for w in setup.risk_factors[:3]:
                lines.append(f"⚠ {w}")
            lines.append(f"🛑 Инвалидация: _{setup.invalidation_condition}_")

        if setup.historical_analog_expectancy_r is not None and setup.analog_sample_size > 0:
            lines.extend([
                "",
                "📚 **ИСТОРИЧЕСКИЕ АНАЛОГИ:**",
                f"• Похожих сетапов в истории: `{setup.analog_sample_size}`",
                f"• Исторический Win Rate: `{setup.analog_win_rate_pct:.0f}%`",
                f"• Историческое матожидание: `{setup.historical_analog_expectancy_r:+.2f}R`",
            ])

        lines.append("━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)

    @staticmethod
    def format_market_overview(tickers: list[dict[str, Any]], breadth: dict[str, Any]) -> str:
        lines = [
            "📊 **ОБЗОР ФЬЮЧЕРСНОГО РЫНКА USDT-M**",
            "━━━━━━━━━━━━━━━━━━━━",
            f"🌐 **Ширина рынка:** `{breadth.get('breadth_state', 'NEUTRAL')}`",
            f"📈 Монет выше EMA 50: `{breadth.get('pct_above_ema50', 50)}%`",
            f"⚖️ Advance / Decline: `{breadth.get('advance_decline_ratio', 1.0)}`",
            "",
            "🪙 **ОСНОВНЫЕ ИНСТРУМЕНТЫ:**",
        ]

        for t in tickers[:8]:
            sym = t.get("symbol", "")
            price = t.get("last_price", 0.0)
            chg = t.get("price_change_24h_percent", 0.0)
            chg_emoji = "🟢" if chg >= 0 else "🔴"
            lines.append(f"{chg_emoji} **{sym}**: `${price:,.2f}` (`{chg:+.2f}%`)")

        lines.append("━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)

    @staticmethod
    def format_backtest_report(metrics: BacktestMetrics, symbol: str, strategy: str) -> str:
        ret_emoji = "🟢" if metrics.net_profit_usd >= 0 else "🔴"
        return "\n".join([
            "━━━━━━━━━━━━━━━━━━━━",
            f"🧪 **ОТЧЕТ О БЭКТЕСТЕ: {symbol}**",
            f"🧠 Стратегия: `{strategy}`",
            "━━━━━━━━━━━━━━━━━━━━",
            f"💰 Начальный баланс: `${metrics.initial_balance:,.2f}`",
            f"{ret_emoji} Конечный капитал: `${metrics.final_equity:,.2f}` (`{metrics.total_return_pct:+.2f}%`)",
            f"📈 Чистый PnL: `${metrics.net_profit_usd:+,.2f}`",
            "",
            "📊 **КЛЮЧЕВЫЕ МЕТРИКИ:**",
            f"• **Win Rate:** `{metrics.win_rate_pct:.1f}%` ({metrics.winning_trades} из {metrics.total_trades} сделок)",
            f"• **Profit Factor:** `{metrics.profit_factor:.2f}`",
            f"• **Sharpe Ratio:** `{metrics.sharpe_ratio:.2f}`",
            f"• **Sortino Ratio:** `{metrics.sortino_ratio:.2f}`",
            f"• **Calmar Ratio:** `{metrics.calmar_ratio:.2f}`",
            f"• **Макс. просадка:** `-{metrics.max_drawdown_pct:.1f}%` (${metrics.max_drawdown_usd:,.2f})",
            f"• **Матожидание (Expectancy):** `{metrics.expectancy_r:+.2f}R`",
            f"• Средний выигрыш: `${metrics.avg_win_usd:,.2f}` | Средний убыток: `${metrics.avg_loss_usd:,.2f}`",
            f"• Комиссии биржи: `-${metrics.total_fees_usd:,.2f}` | Фандинг: `-${metrics.total_funding_usd:,.2f}`",
            f"• Ликвидаций: `{metrics.liquidations_count}`",
            "━━━━━━━━━━━━━━━━━━━━",
        ])

    @staticmethod
    def format_paper_portfolio(portfolio: VirtualPortfolio) -> str:
        pnl_emoji = "🟢" if portfolio.total_unrealized_pnl >= 0 else "🔴"
        lines = [
            "🎮 **ВИРТУАЛЬНЫЙ ПОРТФЕЛЬ (PAPER TRADING)**",
            "━━━━━━━━━━━━━━━━━━━━",
            f"💰 **Общий баланс (Equity):** `${portfolio.total_equity:,.2f}`",
            f"💵 Свободная маржа: `${portfolio.available_balance:,.2f}`",
            f"🔒 Занятая маржа: `${portfolio.margin_used:,.2f}`",
            f"{pnl_emoji} Нереализованный PnL: `${portfolio.total_unrealized_pnl:+,.2f}`",
            f"📈 Общая доходность: `{portfolio.total_return_pct:+.2f}%`",
            f"🏆 Win Rate: `{portfolio.win_rate_pct:.1f}%` ({portfolio.closed_trades_count} закрытых сделок)",
            f"💸 Уплачено комиссий: `${portfolio.total_commission_paid:,.2f}`",
            "",
            "📌 **ОТКРЫТЫЕ ПОЗИЦИИ:**",
        ]

        if not portfolio.open_positions:
            lines.append("_Нет активных позиций._")
        else:
            for pos in portfolio.open_positions.values():
                side_emoji = "🟢" if pos.side == "LONG" else "🔴"
                lines.append(
                    f"{side_emoji} **{pos.symbol}** {pos.side} {pos.leverage}x | Qty: `{pos.quantity:.2f}`\n"
                    f"   Вход: `${pos.entry_price:,.2f}` | Текущая: `${pos.current_price:,.2f}`\n"
                    f"   PnL: `${pos.unrealized_pnl:+,.2f}` | SL: `${pos.stop_loss:,.2f}` | TP1: `${pos.take_profit_1:,.2f}`"
                )

        lines.append("━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)
