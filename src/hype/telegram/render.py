"""Telegram render — rich signal cards (ported from v3 report.py but enhanced)."""

from __future__ import annotations

from datetime import datetime

from ..config import build_line
from ..signals.models import Signal


def render_signal_card(signal: Signal, mode: str = "beginner", version: str | None = None, release: str | None = None) -> str:
    """Render full signal card for Telegram."""

    direction_emoji = "🟢 LONG" if signal.direction == "LONG" else "🔴 SHORT"
    grade_emoji = {"S": "💎", "A": "🔥", "B": "⭐", "C": "📊", "D": "⚠️"}.get(signal.quality_grade, "📊")

    # Header
    lines = [
        f"{direction_emoji} {signal.symbol} — {signal.exchange.upper()}",
        f"{grade_emoji} Оценка сетапа: {signal.quality_score:.0f}/100 ({signal.quality_grade})",
        "",
        f"🎯 УВЕРЕННОСТЬ БОТА: {signal.confidence_pct:.0f}% — {signal.confidence_label}",
        f"{'█' * int(signal.confidence_pct // 10)}{'░' * (10 - int(signal.confidence_pct // 10))} {signal.confidence_pct:.0f} из 100",
        "",
        f"💰 Цена входа: {signal.entry:.6f}" if signal.entry < 1 else f"💰 Цена входа: {signal.entry:.2f}",
        f"📍 Зона входа: {signal.entry_zone_low:.6f} — {signal.entry_zone_high:.6f}" if signal.entry < 1 else f"📍 Зона входа: {signal.entry_zone_low:.2f} — {signal.entry_zone_high:.2f}",
        "",
        f"🛑 Стоп-лосс: {signal.stop_loss:.6f}" if signal.stop_loss < 1 else f"🛑 Стоп-лосс: {signal.stop_loss:.2f}",
        f"   Риск: {abs(signal.entry - signal.stop_loss) / signal.entry * 100:.2f}% | {signal.risk_score}/10",
        "",
        "🎯 Тейк-профиты:",
    ]

    for i, (tp, pct) in enumerate(zip(signal.take_profits, signal.tp_pcts), 1):
        tp_str = f"{tp:.6f}" if tp < 1 else f"{tp:.2f}"
        profit_pct = (tp - signal.entry) / signal.entry * 100 if signal.direction == "LONG" else (signal.entry - tp) / signal.entry * 100
        lines.append(f"  TP{i} {tp_str} ({profit_pct:+.2f}%) — закрыть {pct*100:.0f}%")

    lines.extend(
        [
            "",
            f"📈 R:R = 1:{signal.risk_reward:.2f}",
            f"🚀 Ожидаемый скачок: {signal.expected_move_pct:+.2f}% → {signal.expected_target:.6f}" if signal.expected_target < 1 else f"🚀 Ожидаемый скачок: {signal.expected_move_pct:+.2f}% → {signal.expected_target:.2f}",
            "",
            f"🏛 Режим рынка: {signal.regime}",
            f"⚡ Фаза импульса: {signal.early_phase} (heat {signal.heat:.0f})",
            f"📚 Стакан: imbalance {signal.orderbook_imbalance:.2f}",
            "",
        ]
    )

    if signal.signals_detected:
        lines.append("🔍 Детекторы:")
        for det in signal.signals_detected[:3]:
            lines.append(f"  • {det.get('type')} {det.get('direction')} — {det.get('reason','')[:80]}")
        lines.append("")

    if mode == "pro":
        # PRO details
        lines.append("📊 PRO разбор:")
        if signal.raw_analysis:
            conf = signal.raw_analysis.get("confluence", {})
            if conf:
                lines.append(f"  Консенсус LONG {conf.get('long_score',0):.0f}% vs SHORT {conf.get('short_score',0):.0f}% bias {conf.get('bias')}")
                for vote in conf.get("votes", [])[:5]:
                    lines.append(f"    - {vote.get('name')}: L{vote.get('long')} S{vote.get('short')} — {vote.get('reason','')[:60]}")
            struct = signal.raw_analysis.get("structure", {})
            if struct:
                lines.append(f"  Структура: {struct.get('structure')} HH={struct.get('higher_highs')} HL={struct.get('higher_lows')}")
                if struct.get("support"):
                    lines.append(f"  Поддержки: {', '.join(f'{s:.2f}' for s in struct['support'][:3])}")
                if struct.get("resistance"):
                    lines.append(f"  Сопротивления: {', '.join(f'{r:.2f}' for r in struct['resistance'][:3])}")
            # Risk plan details
            rp = signal.raw_analysis.get("risk_plan")
            if rp:
                lines.append(f"  Плечо: {rp.leverage}x | Ликвидация ~{rp.liquidation_price:.2f}" if rp.liquidation_price else f"  Плечо: {rp.leverage}x")
                lines.append(f"  Позиция: {rp.position_size_pct:.1f}% депо | Риск ${rp.risk_usd:.2f}" if rp.risk_usd else "")
        lines.append("")

    # Reasons
    if signal.reasons:
        lines.append("💡 Почему этот сигнал:")
        for r in signal.reasons[:5]:
            if isinstance(r, dict):
                lines.append(f"  • {r.get('reason','')[:90]}")
            else:
                lines.append(f"  • {str(r)[:90]}")
        lines.append("")

    # Confidence breakdown
    if signal.raw_analysis and signal.raw_analysis.get("confidence"):
        conf_info = signal.raw_analysis["confidence"]
        lines.append("🔍 Из чего сложилась уверенность:")
        for b in conf_info.get("breakdown", [])[:6]:
            lines.append(f"  • {b['name']}: {b['value']:.0f}% (вес {b['weight']*100:.0f}%) — {b['desc']}")
        if conf_info.get("weak_spots"):
            lines.append("")
            lines.append("⚠️ Слабые места:")
            for w in conf_info["weak_spots"][:3]:
                lines.append(f"  • {w}")
        lines.append("")

    # No-trade reasons if present
    if signal.status == "NO_TRADE" and signal.no_trade_reasons:
        lines.append("⛔ NO TRADE — причины:")
        for r in signal.no_trade_reasons:
            lines.append(f"  • {r}")
        lines.append("")

    lines.append("⚠️ Это статистическая оценка, а не гарантия прибыли. DYOR.")
    lines.append(build_line(version, release))

    text = "\n".join(lines)
    # Telegram limit 4096
    if len(text) > 4000:
        text = text[:4000] + "\n... (обрезано)"
    return text


def render_scan_results(signals: list[Signal], title: str = "⭐ Топ возможности") -> str:
    if not signals:
        return f"{title}\n\nНет сигналов, удовлетворяющих фильтрам. Попробуйте снизить пороги или проверить позже."

    lines = [f"{title} — {len(signals)} монет", ""]
    for i, sig in enumerate(signals[:15], 1):
        dir_emoji = "🟢" if sig.direction == "LONG" else "🔴"
        grade = sig.quality_grade
        lines.append(
            f"{i}. {dir_emoji} {sig.symbol} {sig.direction} | {grade} {sig.quality_score:.0f} | conf {sig.confidence_pct:.0f}% | RR 1:{sig.risk_reward:.1f} | {sig.early_phase}"
        )
        lines.append(f"   Вход {sig.entry:.4f} → TP {sig.take_profits[0]:.4f} SL {sig.stop_loss:.4f} | +{sig.expected_move_pct:.1f}%")
    lines.append("")
    lines.append("Нажмите на монету для детального разбора 👇")
    return "\n".join(lines)


def render_market_overview(btc_price: float | None = None, eth_price: float | None = None, gainers: list | None = None) -> str:
    lines = ["📊 Мой рынок", ""]
    if btc_price:
        lines.append(f"BTC: ${btc_price:,.2f}")
    if eth_price:
        lines.append(f"ETH: ${eth_price:,.2f}")
    lines.append("")
    if gainers:
        lines.append("🔥 Топ рост за 24ч:")
        for g in gainers[:10]:
            lines.append(f"  {g['symbol']}: {g['change']:+.2f}%")
    lines.append("")
    lines.append("Обновляется каждые 3 минуты в авто-режиме.")
    return "\n".join(lines)


def render_help() -> str:
    return """
📚 HYPE ULTIMATE — Помощь

🎯 Что умеет бот:
• Сканирует 300+ монет на Binance, Bybit, OKX, MEXC, KuCoin, Gate, Bitget
• Находит ранние импульсы (EARLY/TRIGGERED), а не уже выросшее
• Детекторы STOBB (перепроданность + BB), SBM (STOBB + MA + PSAR), JUMP (резкий объем)
• 11 стратегий голосуют: EMA, RSI, MACD, BB, Donchian, VWAP, Stochastic и др.
• Анализ стакана: дисбаланс, стены, ликвидность
• Считает вход, стоп, 3 тейка, R:R, плечо, ликвидацию, ожидаемый скачок
• Уверенность бота 0-100% из 6 независимых анализов

🔎 СКАНИРОВАТЬ РЫНОК — полный скан вселенной (Stage1 heat → Stage2 глубокий анализ)
🔥 ЛУЧШИЕ LONG/SHORT — топ по направлению
⭐ ТОП ВОЗМОЖНОСТИ — без фильтра направления
🔍 АНАЛИЗ МОНЕТЫ — введи символ, получи полный разбор
🔔 АВТО-СИГНАЛЫ — бот сам пишет, когда находит S/A сетап

📈 Как читать сигнал:
• Оценка S/A/B/C — качество комбинации факторов (не вероятность прибыли!)
• Уверенность — насколько анализы согласны между собой
• R:R — потенциал к риску (минимум 1.8)
• EARLY — база просыпается, ждем подтверждения
• TRIGGERED — пробой подтвержден закрытой свечой
• EXHAUSTED — движение выжато, не догоняем

⚠️ Дисклеймер: любой сигнал — статистическая оценка, не гарантия. Криптофьючерсы высокорискованны.

🛠 Сборка: ULTIMATE v4 — Multi-exchange + STOBB/SBM/JUMP + Liquidity + Confluence
"""


def render_no_access() -> str:
    return "⛔ НЕТ ДОСТУПА — бот закрытый. Добавьте ваш Telegram ID в TELEGRAM_ALLOWED_USER_IDS"
