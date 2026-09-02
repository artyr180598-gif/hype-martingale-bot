"""Авто-сигналы: порог «действительно хороший сетап» + красивая карточка.

Задача раздела: бот сам сканирует рынок и молчит, пока не найдёт сетап, который
проходит ВСЕ пороги качества. Всё, что не дотянуло, сохраняется в SQLite и видно
в разделах «⭐ ТОП / 🔥 LONG / 🔻 SHORT» — но в чат не летит.

Порог намеренно строже основного гейта входа (``validator.validate_signal``):
основной гейт отвечает на «можно ли это показывать», порог авто-сигнала — на
«стоит ли будить пользователя». Оба детерминированные и оба видны в UI, чтобы
не было ощущения «бот молчит без причины».
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from v3.analysis.confidence import ConfidenceReport, assess_confidence
from v3.config import SignalConfig
from v3.models import TradingSignal
from v3.tg.render import (
    confidence_bar,
    confidence_headline,
    data_completeness_line,
    quality_label,
    source_stamp,
)

# События жизненного цикла, по которым тоже пишем в чат (TP/SL).
EVENT_EMOJI = {
    "STOPPED": "🛑",
    "CLOSED": "✅",
    "TP3_HIT": "✅",
    "TP2_HIT": "🎯",
    "TP1_HIT": "🎯",
    "INVALIDATED": "⚠️",
    "EXPIRED": "⌛",
}
EVENT_WORDS = {
    "STOPPED": "стоп-лосс сработал — идея отменена",
    "CLOSED": "все цели достигнуты — позиция закрыта",
    "TP3_HIT": "третья цель достигнута",
    "TP2_HIT": "вторая цель достигнута",
    "TP1_HIT": "первая цель достигнута — зафиксируйте часть прибыли",
    "INVALIDATED": "сценарий отменён рынком",
    "EXPIRED": "сигнал устарел без достижения целей",
}


@dataclass
class AlertDecision:
    """Прошёл ли сетап порог авто-сигнала (и почему нет — человеческим языком)."""

    ok: bool = False
    percent: float = 0.0
    label: str = ""
    reasons: list[str] = field(default_factory=list)
    report: ConfidenceReport | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "bot_confidence": round(self.percent, 1),
            "label": self.label,
            "reasons": self.reasons,
        }


def evaluate_alert(signal: Any, cfg: SignalConfig | None = None) -> AlertDecision:
    """Детерминированная проверка: будить пользователя или нет.

    Никогда не бросает исключение и не ходит в сеть: вход — уже посчитанный
    сигнал со своими ``features`` и ``score_breakdown``.
    """
    cfg = cfg or SignalConfig()
    report = assess_confidence(signal, cfg)
    reasons: list[str] = []

    direction = str(getattr(signal, "direction", ""))
    if direction not in ("LONG", "SHORT"):
        reasons.append("нет направления: бот не видит чистого входа")
    if str(getattr(signal, "status", "")) in ("NO_TRADE", "INVALIDATED", "EXPIRED", "STOPPED", "CLOSED"):
        reasons.append(f"статус сетапа {getattr(signal, 'status', '')}")

    if cfg.ALERT_REQUIRE_FRESH and bool(getattr(signal, "stale", False)):
        reasons.append("данные устарели — сигнал неактуален")

    quality = float(getattr(signal, "quality", 0.0) or 0.0)
    if quality < cfg.ALERT_MIN_QUALITY:
        reasons.append(f"оценка сетапа {quality:.0f}/100 ниже порога {cfg.ALERT_MIN_QUALITY:.0f}")

    if report.percent < cfg.ALERT_MIN_BOT_CONFIDENCE:
        reasons.append(
            f"уверенность бота {report.percent:.0f}% ниже порога "
            f"{cfg.ALERT_MIN_BOT_CONFIDENCE:.0f}%"
        )

    completeness = float(getattr(signal, "confidence", 0.0) or 0.0)
    if completeness < cfg.ALERT_MIN_DATA_CONFIDENCE:
        reasons.append(
            f"полнота данных {completeness * 100:.0f}% ниже порога "
            f"{cfg.ALERT_MIN_DATA_CONFIDENCE * 100:.0f}%"
        )

    risk = int(getattr(signal, "risk_score", 10) or 10)
    if risk > cfg.ALERT_MAX_RISK_SCORE:
        reasons.append(f"риск {risk}/10 выше порога {cfg.ALERT_MAX_RISK_SCORE}/10")

    rr = float(getattr(signal, "rr", 0.0) or 0.0)
    if rr < cfg.ALERT_MIN_RR:
        reasons.append(f"потенциал к риску 1:{rr:.1f} ниже порога 1:{cfg.ALERT_MIN_RR:.1f}")

    emergence = (getattr(signal, "features", None) or {}).get("emergence") or {}
    if str(emergence.get("phase", "")) == "EXHAUSTED":
        reasons.append("движение уже выжато — догонять поздно")

    stop_loss = float(getattr(signal, "stop_loss", 0.0) or 0.0)
    targets = list(getattr(signal, "targets", []) or [])
    if stop_loss <= 0:
        reasons.append("нет стоп-лосса")
    if len(targets) < 2:
        reasons.append("меньше двух целей — план неполный")

    return AlertDecision(
        ok=not reasons,
        percent=report.percent,
        label=report.label,
        reasons=reasons,
        report=report,
    )


# ── пауза после серии стопов ────────────────────────────────────
def stopout_pause(
    outcomes: list[dict[str, Any]],
    cfg: SignalConfig | None = None,
    now_ms: int | None = None,
) -> tuple[bool, str]:
    """Гасить ли авто-сигнал по монете после серии стопов.

    Аналог ``PerformanceFilter``/``PairInformationFilter`` из freqtrade: там
    монету выводят из торговли после плохих результатов, а не продолжают слать
    по ней сигналы. У нас то же самое, но мягче — гасится только уведомление,
    анализ и запись в базу продолжаются.

    ``outcomes`` — строки ``store.outcomes(symbol)`` (от новых к старым).
    Пауза включается, если последние ``ALERT_STOPOUT_GUARD`` ЗАКРЫТЫХ сделок
    подряд закрылись по стопу и с последнего стопа не прошло
    ``ALERT_STOPOUT_PAUSE_HOURS``. Возвращает ``(пауза?, объяснение)``.
    """
    cfg = cfg or SignalConfig()
    limit = int(cfg.ALERT_STOPOUT_GUARD or 0)
    if limit <= 0:
        return False, ""

    closed = [
        o for o in (outcomes or [])
        if str(o.get("outcome") or "").upper() not in ("", "OPEN")
    ]
    if len(closed) < limit:
        return False, ""
    recent = closed[:limit]
    stopped = all(
        str(o.get("outcome") or "").upper() == "LOSS"
        or "stop" in str(o.get("exit_reason") or "").lower()
        for o in recent
    )
    if not stopped:
        return False, ""

    now = int(now_ms if now_ms is not None else time.time() * 1000)
    last_exit = max(int(o.get("exit_at") or 0) for o in recent)
    pause_ms = float(cfg.ALERT_STOPOUT_PAUSE_HOURS) * 3_600_000
    age_ms = now - last_exit
    if last_exit and age_ms > pause_ms:
        return False, ""

    left_h = max(0.0, (pause_ms - max(0, age_ms)) / 3_600_000)
    return True, (
        f"монета на паузе: {limit} стопа подряд, последний "
        f"{max(0, age_ms) / 3_600_000:.1f} ч назад — не будим ещё {left_h:.1f} ч"
    )


# ── карточка авто-сигнала ───────────────────────────────────────
def _target_line(signal: TradingSignal) -> str:
    entry_low, entry_high = signal.entry_zone or (0.0, 0.0)
    entry_mid = (entry_low + entry_high) / 2 if entry_high else signal.price
    if not signal.targets or not entry_mid:
        return ""
    parts = []
    for t in signal.targets[:3]:
        pct = (t / entry_mid - 1.0) * 100.0 if signal.direction == "LONG" else (1.0 - t / entry_mid) * 100.0
        parts.append(f"{t:.6g} ({pct:+.1f}%)")
    return " → ".join(parts)


def render_signal_alert(signal: TradingSignal, cfg: SignalConfig | None = None) -> str:
    """Карточка, которая приходит сама: уверенность, план сделки, почему."""
    cfg = cfg or SignalConfig()
    report = assess_confidence(signal, cfg)
    emoji = "🟢" if signal.direction == "LONG" else "🔻"
    side = "LONG — ставка на рост" if signal.direction == "LONG" else "SHORT — ставка на падение"
    entry_low, entry_high = signal.entry_zone or (0.0, 0.0)
    entry_mid = (entry_low + entry_high) / 2 if entry_high else signal.price
    stop_pct = (
        abs(entry_mid - signal.stop_loss) / entry_mid * 100.0
        if entry_mid and signal.stop_loss
        else 0.0
    )
    rb = signal.risk_brief
    risk_pct = float(getattr(rb, "max_deposit_pct", 0.0) or 0.0)
    leverage = int(getattr(signal, "leverage", 0) or (getattr(rb, "leverage", 1) or 1))

    lines = [
        f"🚨 **АВТО-СИГНАЛ** · {emoji} **{signal.symbol}** — {side}",
        "",
        confidence_headline(report),
        f"{confidence_bar(report.percent)} {report.percent:.0f} из 100 · {report.verdict}",
        source_stamp(signal.source, signal.ts_ms, signal.data_age_seconds),
        "",
        f"⭐ Оценка сетапа: {quality_label(signal.quality, signal.tier, cfg)}",
        data_completeness_line(signal),
        "",
        "💰 **План сделки:**",
        f"• Вход: {entry_low:.6g}–{entry_high:.6g}",
        f"• Стоп-лосс: {signal.stop_loss:.6g} (−{stop_pct:.1f}% от входа) — идея отменена",
    ]
    targets = _target_line(signal)
    if targets:
        lines.append(f"• Цели: {targets}")
    tail = f"• Потенциал к риску 1:{signal.rr:.1f} · плечо до {leverage}x"
    if risk_pct:
        tail += f" · риск ≈ {risk_pct:.1f}% депозита"
    lines.append(tail)

    lines += ["", "🔍 **Почему бот уверен** (вес анализа в цифре):"]
    for part in report.parts:
        lines.append(f"• {part.title}: {part.score:.0f}% (вес {part.weight * 100:.0f}%) — {part.note}")

    human_risks = [r for r in (signal.risks or []) if not r.startswith(("stop distance", "priority"))][:3]
    if human_risks:
        lines += ["", "⚠️ **На что смотреть:**"]
        lines += [f"• {r}" for r in human_risks]
    if report.warnings:
        lines += ["", "📉 **Что снижает уверенность:**"]
        lines += [f"• {w}" for w in report.warnings]

    lines += [
        "",
        "❗ Авто-сигнал — аналитика, не гарантия результата и не приказ входить. "
        "Решение и риск на вас.",
    ]
    return "\n".join(lines)


def render_event_alert(event: dict[str, Any], cfg: SignalConfig | None = None) -> str:
    """Событие по активному сигналу: цель достигнута / стоп сработал."""
    name = str(event.get("event", ""))
    emoji = EVENT_EMOJI.get(name, "📊")
    words = EVENT_WORDS.get(name, name.lower().replace("_", " "))
    r_multiple = event.get("r_multiple")
    r_txt = f" · результат {float(r_multiple):+.2f}R" if r_multiple not in (None, 0) else ""
    price = event.get("price")
    price_txt = f" · цена {float(price):.6g}" if price else ""
    return "\n".join([
        f"{emoji} **{event.get('symbol', '?')}** — {words}{r_txt}{price_txt}",
        "❗ Аналитика, не гарантия результата.",
    ])


# ── транспорт-агностичный контейнер ─────────────────────────────
@dataclass
class AlertItem:
    """Что watcher передаёт в канал доставки (Telegram / лог / webhook)."""

    kind: str = "signal"                 # signal | event
    signal: TradingSignal | None = None
    event: dict[str, Any] | None = None
    decision: AlertDecision | None = None

    @property
    def symbol(self) -> str:
        if self.signal is not None:
            return self.signal.symbol
        return str((self.event or {}).get("symbol", "?"))

    def to_dict(self) -> dict[str, Any]:
        if self.kind == "signal" and self.signal is not None:
            payload = self.signal.to_dict()
            payload["alert"] = self.decision.to_dict() if self.decision else None
            return payload
        return {"event": self.event or {}, "alert": None}


def render_alert(item: Any, cfg: SignalConfig | None = None) -> str:
    """Текст уведомления для ``AlertItem`` (или для legacy dict)."""
    cfg = cfg or SignalConfig()
    if isinstance(item, AlertItem):
        if item.kind == "signal" and item.signal is not None:
            return render_signal_alert(item.signal, cfg)
        return render_event_alert(item.event or {}, cfg)
    # legacy-контракт: список словарей (sig.to_dict() / событие)
    data = item if isinstance(item, dict) else {}
    if data.get("event"):
        return render_event_alert(data, cfg)
    if data.get("direction") in ("LONG", "SHORT"):
        signal = TradingSignal(
            uid=str(data.get("uid", "")),
            symbol=str(data.get("symbol", "")),
            ts_ms=int(data.get("ts_ms", 0) or 0),
            direction=data["direction"],
            status=data.get("status", "CONFIRMED"),
            entry_zone=tuple(data.get("entry_zone") or (0.0, 0.0)),
            stop_loss=float(data.get("stop_loss", 0.0) or 0.0),
            targets=list(data.get("targets") or []),
            rr=float(data.get("rr", 0.0) or 0.0),
            tier=data.get("tier", "NONE"),
            quality=float(data.get("quality", 0.0) or 0.0),
            confidence=float(data.get("confidence", 0.0) or 0.0),
            risk_score=int(data.get("risk_score", 5) or 5),
            leverage=int(data.get("leverage", 1) or 1),
            price=float(data.get("price", 0.0) or 0.0),
            source=str(data.get("source", "") or ""),
            features=dict(data.get("features") or {}),
            risks=list(data.get("risks") or []),
            data_age_seconds=data.get("data_age_seconds"),
            stale=bool(data.get("stale", False)),
        )
        return render_signal_alert(signal, cfg)
    return ""
