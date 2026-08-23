"""
Deterministic AI Market Intelligence Analyst.
"""
from src.scanner.market_scanner import MarketScanner
from src.signals.models import SignalSetup


class AIAssistant:
    """
    Translates quantitative feature matrices and statistical signals into natural language market insights.
    Strict Rule: All numbers, prices, and regimes are rooted in verifiable deterministic features.
    """

    @classmethod
    async def process_user_query(cls, query: str) -> str:
        q = query.strip().lower()
        scanner = MarketScanner()

        # Check if query asks for a specific coin
        for sym in ["btc", "eth", "sol", "bnb", "xrp", "doge", "avax", "link", "sui"]:
            if sym in q:
                full_sym = f"{sym.upper()}USDT"
                setup = await scanner.scan_symbol(full_sym)
                if not setup:
                    return f"⚠️ Данные по {full_sym} временно недоступны. Проверьте подключение к бирже."
                return cls.format_coin_analysis_nl(setup)

        # Quantitative Educational & Architectural Explanations
        if any(w in q for w in ["order flow", "ордер флоу", "cvd", "дельта", "volume delta"]):
            return (
                "📊 **Order Flow & Cumulative Volume Delta (CVD):**\n\n"
                "• **CVD (Cumulative Volume Delta)** измеряет разницу между рыночными покупками (Taker Buy) и продажами (Taker Sell).\n"
                "• **CVD Дивергенция**: Если цена обновляет High, а CVD падает — это признак истощения покупателей и скрытого поглощения лимитными ордерами.\n"
                "• Платформа отслеживает 10-периодный наклон CVD и соотношение тейкеров для подтверждения пробоев."
            )

        if any(w in q for w in ["funding", "фандинг", "squeeze", "сквиз"]):
            return (
                "⚡ **Funding Squeeze & Ставка финансирования:**\n\n"
                "• Отрицательный фандинг ($< -0.03\%$) при растущем открытом интересе (OI) указывает на переполненный шорт-позициями рынок.\n"
                "• Это создает предпосылки для каскадного **Short Squeeze**, где принудительное закрытие шортов толкает цену вверх.\n"
                "• Платформа оценивает Z-score фандинга за 30-дневное окно для исключения ложных сигналов."
            )

        if any(w in q for w in ["риск", "risk", "плечо", "leverage", "stop loss", "стоп"]):
            return (
                "🛡️ **Институциональный Риск-Менеджмент:**\n\n"
                "• **Размер позиции**: Рассчитывается строго от допустимого риска депозита (1.5% по умолчанию) и дистанции до структурного стоп-лосса.\n"
                "• **Динамическое плечо**: Вычисляется так, чтобы расчетная цена ликвидации была как минимум в 2.5 раза дальше уровня стоп-лосса.\n"
                "• **Circuit Breaker**: Автоматическая остановка торговли при дневной просадке депозита $\\ge 10\%$."
            )

        if any(w in q for w in ["структур", "structure", "bos", "choch", "fvg", "ликвидность"]):
            return (
                "📐 **Рыночная структура (SMC & Market Structure):**\n\n"
                "• **BOS (Break of Structure)**: Пробой предыдущего экстремума по тренду с закреплением телом свечи.\n"
                "• **CHoCH (Change of Character)**: Первый слом структуры против текущего тренда, сигнализирующий о развороте.\n"
                "• **Liquidity Sweeps**: Снятие стоп-лоссов за ключевыми уровнями с мгновенной реакцией цены."
            )

        # General market overview query
        if any(w in q for w in ["рынок", "market", "сетапы", "top", "лучшие", "сигналы", "short", "long"]):
            setups = await scanner.scan_market()
            return cls.format_market_summary_nl(setups)

        return (
            "🤖 Я готов предоставить количественный анализ рынка.\n\n"
            "Примеры вопросов:\n"
            "• *Проанализируй BTC*\n"
            "• *Какие лучшие сетапы на рынке?*\n"
            "• *Что такое Order Flow и CVD?*\n"
            "• *Как работает Funding Squeeze?*\n"
            "• *Объясни правила риск-менеджмента и расчет плеча*"
        )

    @classmethod
    def format_coin_analysis_nl(cls, setup: SignalSetup) -> str:
        sb = setup.score_breakdown
        lines = [
            f"🧠 **Количественный отчет: {setup.symbol}**",
            f"📊 **Режим рынка:** `{setup.market_regime}`",
            f"🎯 **Сценарий:** `{setup.direction.value}` (Score: {setup.score:.1f}/100)",
            f"⚡ **Рекомендуемое плечо:** `{setup.recommended_leverage}x`",
            "",
            "📐 **Ключевые уровни:**",
            f"• Вход: `{setup.entry_zone}`",
            f"• Стоп-лосс: `${setup.stop_loss:,.2f}`",
            f"• Тейк 1: `${setup.take_profit_1:,.2f}`",
            f"• R:R: `1:{setup.risk_reward_ratio:.1f}`",
            "",
            "🔍 **Почему?**",
        ]
        for r in setup.primary_reasons[:3]:
            lines.append(f"• {r}")

        if setup.risk_factors:
            lines.append("\n⚠️ **Факторы риска:**")
            for w in setup.risk_factors[:2]:
                lines.append(f"• {w}")

        if setup.historical_analog_expectancy_r is not None and setup.analog_sample_size > 0:
            lines.append(
                f"\n📚 **Исторические аналоги:** найдено {setup.analog_sample_size} похожих паттернов "
                f"с матожиданием `{setup.historical_analog_expectancy_r:+.2f}R` (Win Rate: {setup.analog_win_rate_pct:.0f}%)"
            )

        return "\n".join(lines)

    @classmethod
    def format_market_summary_nl(cls, setups: list[SignalSetup]) -> str:
        if not setups:
            return "Рынок в нейтральном состоянии, активных подтвержденных сетапов нет."

        lines = ["🔥 **ТОП ТОРГОВЫХ СЕТАПОВ НА ФЬЮЧЕРСАХ:**\n"]
        for i, s in enumerate(setups[:5], 1):
            dir_emoji = "🟢" if s.direction.value == "LONG" else ("🔴" if s.direction.value == "SHORT" else "⚪")
            lines.append(
                f"{i}. {dir_emoji} **{s.symbol}** — `{s.direction.value}` | Score: `{s.score:.0f}/100`\n"
                f"   Вход: `{s.entry_zone}` | SL: `${s.stop_loss:,.2f}` | R:R `1:{s.risk_reward_ratio:.1f}`\n"
                f"   Режим: `{s.market_regime}`\n"
            )
        return "\n".join(lines)
