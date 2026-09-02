"""«Уверенность бота» — сводный процент с честным разбором по анализам.

Раньше пользователь видел только «Оценку сетапа 72/100» и «Уверенность в
данных 0.8/1»: две разные сущности со схожими названиями и ни одной цифры,
которую можно было бы прочитать как «насколько бот уверен». Отсюда путаница и
ощущение, что «процент уверенности пропал».

Этот модуль вводит ТРЕТЬЮ, явно названную метрику и не заменяет две старые:

  1. **Оценка сетапа (0..100)** — качество комбинации факторов (``score_signal``).
  2. **Полнота данных (0..1)** — сколько реальных источников получено
     (``engine.data_confidence``).
  3. **Уверенность бота (0..100%)** — этот модуль: взвешенная сводка того,
     насколько *независимые* анализы бота согласны между собой.

Важно и зафиксировано текстом в UI: уверенность бота — это НЕ вероятность
прибыли и НЕ обещание движения. Это мера согласованности доказательств
(тренд, структура, объёмы, стакан, деривативы, риск, ранний импульс) и
полноты данных.

Модуль чистый (без I/O) и работает как для live-сигнала, так и для бэктеста:
на входе — уже посчитанный ``TradingSignal`` со своими ``features``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from v3.config import SignalConfig

# Ключи компонентов. Совпадают с ключами в SignalConfig.bot_confidence_weights.
PART_QUALITY = "quality"
PART_DATA = "data"
PART_TREND = "trend"
PART_CONFIRM = "confirm"
PART_RISK = "risk"
PART_IMPULSE = "impulse"

PART_TITLES: dict[str, str] = {
    PART_QUALITY: "Качество сетапа",
    PART_DATA: "Свежесть и полнота данных",
    PART_TREND: "Согласованность таймфреймов",
    PART_CONFIRM: "Объём, стакан и позиции",
    PART_RISK: "Риск-профиль",
    PART_IMPULSE: "Ранняя готовность импульса",
}

# Что анализ стоит за компонентом — объяснение для новичка (коротко).
PART_SOURCES: dict[str, str] = {
    PART_QUALITY: "оценка сетапа движка",
    PART_DATA: "реальные данные биржи",
    PART_TREND: "тренды 5m/15m/1h/4h/1d",
    PART_CONFIRM: "объёмы, стакан, OI и фандинг",
    PART_RISK: "риск и потенциал к риску",
    PART_IMPULSE: "ранний отбор «намечающегося движения»",
}


def _clip(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(v)))


def _plural(count: int, one: str, few: str, many: str) -> str:
    """«1 таймфрейм / 3 таймфрейма / 5 таймфреймов» — без «4 таймфреймов»."""
    n = abs(int(count)) % 100
    if 11 <= n <= 14:
        return f"{count} {many}"
    last = n % 10
    if last == 1:
        return f"{count} {one}"
    if 2 <= last <= 4:
        return f"{count} {few}"
    return f"{count} {many}"


@dataclass
class ConfidencePart:
    """Один анализ в разборе уверенности."""

    key: str
    title: str
    score: float                 # 0..100 — насколько этот анализ «за» идею
    weight: float                # 0..1 — вклад в итоговый процент
    note: str = ""               # человеческая расшифровка цифры
    source: str = ""             # на какие данные опирается

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "score": round(self.score, 1),
            "weight": round(self.weight, 4),
            "note": self.note,
            "source": self.source,
        }


@dataclass
class ConfidenceReport:
    """Итог: процент, слово-оценка и разбор по анализам."""

    percent: float = 0.0
    label: str = "низкая"
    verdict: str = ""
    parts: list[ConfidencePart] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def weakest(self) -> list[ConfidencePart]:
        """Два самых слабых анализа — то, что реально снижает уверенность."""
        weak = [p for p in self.parts if p.score < 50.0]
        weak.sort(key=lambda p: p.score)
        return weak[:2]

    def part(self, key: str) -> ConfidencePart | None:
        return next((p for p in self.parts if p.key == key), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "percent": round(self.percent, 1),
            "label": self.label,
            "verdict": self.verdict,
            "parts": [p.to_dict() for p in self.parts],
            "warnings": self.warnings,
        }


# ── компоненты ──────────────────────────────────────────────────
def _part_quality(signal: Any) -> tuple[float, str]:
    quality = float(getattr(signal, "quality", 0.0) or 0.0)
    tier = str(getattr(signal, "tier", "") or "")
    tail = f" ({tier})" if tier and tier != "NONE" else ""
    return _clip(quality), f"оценка сетапа {quality:.0f}/100{tail}"


def _part_data(signal: Any, cfg: SignalConfig) -> tuple[float, str]:
    """Полнота/свежесть реальных данных. Устаревшие данные режут уверенность вдвое."""
    completeness = float(getattr(signal, "confidence", 0.0) or 0.0)
    score = _clip(completeness * 100.0)
    age = getattr(signal, "data_age_seconds", None)
    views = (getattr(signal, "features", None) or {}).get("timeframes") or []
    age_txt = f"{age:.0f}с назад" if age is not None else "возраст неизвестен"
    note = f"{_plural(len(views), 'таймфрейм', 'таймфрейма', 'таймфреймов')} получено, данные {age_txt}"
    if bool(getattr(signal, "stale", False)):
        score *= 0.5
        note += " — данные устарели, уверенность снижена вдвое"
    elif age is not None and age > cfg.MAX_DATA_AGE_SECONDS:
        score *= 0.7
        note += f" — данные старее нормы ({cfg.MAX_DATA_AGE_SECONDS:.0f}с)"
    if completeness < 0.7:
        note += " — часть источников не ответила"
    return _clip(score), note


def _part_trend(signal: Any) -> tuple[float, str]:
    """Сколько таймфреймов смотрят в сторону сделки (без внутренних переменных)."""
    views = (getattr(signal, "features", None) or {}).get("timeframes") or []
    direction = str(getattr(signal, "direction", ""))
    if not views or direction not in ("LONG", "SHORT"):
        return 50.0, "таймфреймы не подтверждают одну сторону"
    want = "up" if direction == "LONG" else "down"
    aligned = [v for v in views if str(v.get("trend")) == want]
    ratio = len(aligned) / len(views)
    score = _clip(ratio * 100.0)
    regime = ((getattr(signal, "features", None) or {}).get("regime") or {})
    conflicts = regime.get("conflicts") or []
    if conflicts:
        score = _clip(score - 20.0)
        note = (
            f"{len(aligned)} из {len(views)} таймфреймов в сторону сделки, "
            "но старшие таймфреймы противоречат младшим"
        )
    else:
        note = f"{len(aligned)} из {len(views)} таймфреймов в сторону сделки"
    return score, note


def _part_confirm(signal: Any) -> tuple[float, str]:
    """Объёмы + стакан + деривативы: подтверждают ли идею реальные деньги."""
    features = getattr(signal, "features", None) or {}
    breakdown = getattr(signal, "score_breakdown", None)
    scores: list[float] = []
    if breakdown is not None and getattr(breakdown, "factors", None):
        for f in breakdown.factors:
            if f.name in ("Volume", "Order Flow", "Derivatives") and f.weight > 0:
                scores.append(_clip(f.value / f.weight * 100.0))
    if not scores:
        of = features.get("orderflow") or {}
        der = features.get("derivatives") or {}
        grade = str(of.get("liquidity_grade", ""))
        scores.append({"excellent": 90.0, "ok": 70.0, "thin": 35.0}.get(grade, 50.0))
        scores.append(_clip(float(der.get("positioning_score", 50.0) or 50.0)))
    score = sum(scores) / len(scores)

    notes: list[str] = []
    of = features.get("orderflow") or {}
    der = features.get("derivatives") or {}
    grade = str(of.get("liquidity_grade", ""))
    if grade in ("excellent", "ok"):
        notes.append("стакан плотный")
    elif grade == "thin":
        notes.append("стакан тонкий")
    positioning = str(der.get("positioning", "unknown"))
    pos_word = {
        "healthy_long": "в монету заходят деньги",
        "short_build": "набирают позиции на падение",
        "overheated_long": "лонги перегреты",
        "capitulation": "признак капитуляции",
        "short_squeeze": "шорты выкупают",
    }.get(positioning)
    if pos_word:
        notes.append(pos_word)
    funding_trend = str(der.get("funding_trend", "unknown"))
    if funding_trend in ("neutral", "falling"):
        notes.append("фандинг без перегрева")
    elif funding_trend.startswith("overheated"):
        notes.append("фандинг перегрет")
    return _clip(score), "; ".join(notes) if notes else "подтверждение объёмом и позициями нейтральное"


def _part_risk(signal: Any, cfg: SignalConfig) -> tuple[float, str]:
    """Риск-профиль: чем ниже риск и выше потенциал к риску, тем увереннее."""
    risk_score = float(getattr(signal, "risk_score", 5) or 5)
    rr = float(getattr(signal, "rr", 0.0) or 0.0)
    risk_component = _clip((10.0 - risk_score) / 10.0 * 100.0)
    rr_component = _clip((rr - 1.0) / 2.0 * 100.0)
    score = 0.6 * risk_component + 0.4 * rr_component
    return _clip(score), f"риск {risk_score:.0f}/10, потенциал к риску 1:{rr:.1f}"


def _part_impulse(signal: Any) -> tuple[float, str]:
    """Ранний отбор «намечающегося движения»: совпал ли он с направлением сделки."""
    em = (getattr(signal, "features", None) or {}).get("emergence") or {}
    if not em:
        return 50.0, "ранний отбор не оценивался по этой монете"
    ignition = float(em.get("ignition", 0.0) or 0.0)
    phase = str(em.get("phase", "NEUTRAL"))
    early = str(em.get("early_direction", "FLAT"))
    direction = str(getattr(signal, "direction", ""))
    if phase == "EXHAUSTED":
        return 20.0, f"движение уже далеко (подогрев {ignition:.0f}/100) — догонять поздно"
    if direction in ("LONG", "SHORT") and early == direction:
        score = 55.0 + 45.0 * (ignition / 100.0)
        note = f"ранние признаки совпали с направлением (подогрев {ignition:.0f}/100)"
    elif direction in ("LONG", "SHORT") and early in ("LONG", "SHORT"):
        score = 15.0
        note = f"ранний отбор смотрит в другую сторону (подогрев {ignition:.0f}/100)"
    else:
        score = 40.0 + 30.0 * (ignition / 100.0)
        note = f"ранние признаки есть, направление не подтверждено (подогрев {ignition:.0f}/100)"
    return _clip(score), note


# ── итоговый расчёт ─────────────────────────────────────────────
def assess_confidence(signal: Any, cfg: SignalConfig | None = None) -> ConfidenceReport:
    """Сводный процент уверенности + разбор по каждому анализу.

    Детерминированная функция от уже посчитанного сигнала: никаких новых
    сетевых запросов, поэтому live и бэктест видят одинаковую цифру.
    """
    cfg = cfg or SignalConfig()
    weights = cfg.bot_confidence_weights
    # Все сборщики — zero-arg замыкания: одинаковая сигнатура защищает от
    # «анализ недоступен» из-за рассинхрона вызова и объявления.
    builders = {
        PART_QUALITY: lambda: _part_quality(signal),
        PART_DATA: lambda: _part_data(signal, cfg),
        PART_TREND: lambda: _part_trend(signal),
        PART_CONFIRM: lambda: _part_confirm(signal),
        PART_RISK: lambda: _part_risk(signal, cfg),
        PART_IMPULSE: lambda: _part_impulse(signal),
    }
    parts: list[ConfidencePart] = []
    total = 0.0
    for key, builder in builders.items():
        try:
            score, note = builder()
        except Exception as exc:  # noqa: BLE001 — разбор не должен ронять отчёт
            score, note = 50.0, f"анализ недоступен ({type(exc).__name__})"
        weight = float(weights.get(key, 0.0))
        parts.append(ConfidencePart(
            key=key,
            title=PART_TITLES.get(key, key),
            score=round(score, 1),
            weight=weight,
            note=note,
            source=PART_SOURCES.get(key, ""),
        ))
        total += score * weight

    percent = round(_clip(total), 1)
    if signal is not None and str(getattr(signal, "direction", "")) not in ("LONG", "SHORT"):
        percent = min(percent, 45.0)  # нет направления — высокой уверенности не бывает

    if percent >= cfg.BOT_CONFIDENCE_HIGH_MIN:
        label = "высокая"
        verdict = "независимые анализы бота в основном согласны — сетап сильный"
    elif percent >= cfg.BOT_CONFIDENCE_MEDIUM_MIN:
        label = "умеренная"
        verdict = "идея рабочая, но часть анализов её не подтверждает — вход только с дисциплиной"
    elif percent >= cfg.BOT_CONFIDENCE_LOW_MIN:
        label = "низкая"
        verdict = "сетап слабый: лучше наблюдать, чем входить"
    else:
        label = "очень низкая"
        verdict = "анализы противоречат друг другу — вход не рекомендуется"

    report = ConfidenceReport(percent=percent, label=label, verdict=verdict, parts=parts)
    report.warnings = [f"{p.title}: {p.note}" for p in report.weakest]
    return report


def confidence_percent(signal: Any, cfg: SignalConfig | None = None) -> float:
    """Только цифра (0..100) — для гейтов и сортировки."""
    return assess_confidence(signal, cfg).percent


def attach_confidence(signal: Any, cfg: SignalConfig | None = None) -> ConfidenceReport:
    """Положить разбор в ``signal.features['bot_confidence']``.

    Так цифра попадает в SQLite/API вместе с сигналом и её можно проверить
    постфактум («почему бот был уверен на 81%?»).
    """
    report = assess_confidence(signal, cfg)
    try:
        signal.features["bot_confidence"] = report.to_dict()
    except Exception:  # noqa: BLE001 — features может быть не dict у фейков
        pass
    return report
