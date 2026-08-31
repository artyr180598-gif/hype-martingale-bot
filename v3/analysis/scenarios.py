"""Дополнительные торговые сценарии поверх трендового ядра.

Трендовый путь (regime + голосование ТФ) остаётся главным. Здесь — честные
альтернативы, работающие только на реальных признаках рынка:

  * ``reversal_choch``    — смена характера структуры (CHoCH) на entry/1h ТФ;
  * ``liquidity_sweep``   — «вынос стопов»: пробой уровня фитилём с возвратом;
  * ``range_reversion``   — возврат к середине диапазона от его границы;
  * ``breakout_watch``    — условный сетап: «вход при закрытии выше/ниже X».

Каждый сценарий — кандидат, а не сигнал: дальше срабатывают те же гейты
качества/риска/спреда (для разворотов лишь мягче порог R:R, но не отключён).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from v3.config import SignalConfig
from v3.models import OrderFlowSnapshot, RegimeSnapshot, TimeframeView


@dataclass
class ScenarioCandidate:
    kind: str                                  # reversal_choch | liquidity_sweep | range_reversion | breakout_watch
    direction: str                             # LONG | SHORT
    stop_hint: float | None = None             # подсказка стопа (за структурой / фитилём)
    condition: str = ""                        # условный сетап: вход по условию
    reasons: list[str] = field(default_factory=list)


def _fclose(df: pd.DataFrame) -> float:
    try:
        return float(df["close"].iloc[-1])
    except Exception:  # noqa: BLE001
        return 0.0


def detect_choch_reversal(
    views: list[TimeframeView],
    df_entry: pd.DataFrame | None,
    regime: RegimeSnapshot,
) -> ScenarioCandidate | None:
    """CHoCH на entry/среднем ТФ как ранний разворотный вход.

    Подтверждение: последнее закрытие уже за пределами сломанной структуры
    (для лонга — выше предыдущего swing low зоны, для шорта — ниже swing high).
    """
    if not views or df_entry is None or len(df_entry) < 5:
        return None
    entry = views[0]
    mid = views[1] if len(views) > 1 else None
    close = _fclose(df_entry)
    if close <= 0:
        return None

    choch_up = entry.structure_signal == "CHoCH_UP" or (mid is not None and mid.structure_signal == "CHoCH_UP")
    choch_down = entry.structure_signal == "CHoCH_DOWN" or (mid is not None and mid.structure_signal == "CHoCH_DOWN")

    if choch_up and not choch_down:
        # вход против текущего движения вверх: лонг после смены характера вверх,
        # только если макро не ярко медвежье (иначе это ловля ножей)
        macro = views[-1]
        if macro.trend == "down" and macro.adx >= 30:
            return None
        stop = entry.last_swing_low or entry.support
        return ScenarioCandidate(
            kind="reversal_choch",
            direction="LONG",
            stop_hint=stop,
            reasons=[
                "смена структуры (CHoCH): минимумы перестали обновляться",
                "ранний разворотный сценарий — вход против истощившегося движения",
            ],
        )
    if choch_down and not choch_up:
        macro = views[-1]
        if macro.trend == "up" and macro.adx >= 30:
            return None
        stop = entry.last_swing_high or entry.resistance
        return ScenarioCandidate(
            kind="reversal_choch",
            direction="SHORT",
            stop_hint=stop,
            reasons=[
                "смена структуры (CHoCH): максимумы перестали обновляться",
                "ранний разворотный сценарий — вход против истощившегося движения",
            ],
        )
    return None


def detect_liquidity_sweep(
    df_entry: pd.DataFrame | None,
    view: TimeframeView | None,
    lookback: int = 3,
) -> ScenarioCandidate | None:
    """Пробой уровня фитилём с возвратом (stop-hunt) → вход в сторону возврата.

    Sweep сопротивления (фитиль выше swing high, закрытие вернулось ниже) —
    SHORT со стопом за фитилём. Sweep поддержки — зеркальный LONG.
    """
    if df_entry is None or view is None or len(df_entry) < lookback + 2:
        return None
    tail = df_entry.tail(lookback)
    close = _fclose(df_entry)
    if close <= 0:
        return None

    swing_high = view.last_swing_high if view.last_swing_high and view.last_swing_high > 0 else None
    swing_low = view.last_swing_low if view.last_swing_low and view.last_swing_low > 0 else None

    if swing_high is not None and swing_high < close * 1.02:
        wick = float(tail["high"].max())
        # уровень должен быть «над» текущей ценой в разумных пределах
        if wick > swing_high >= close and bool((tail["high"] > swing_high).any()):
            return ScenarioCandidate(
                kind="liquidity_sweep",
                direction="SHORT",
                stop_hint=wick,
                reasons=[
                    "вынос ликвидности над уровнем: пробой фитилём и возврат закрытием",
                    "ложный пробой — заходы за уровень не удержались",
                ],
            )
    if swing_low is not None and swing_low > close * 0.98:
        wick = float(tail["low"].min())
        if wick < swing_low <= close and bool((tail["low"] < swing_low).any()):
            return ScenarioCandidate(
                kind="liquidity_sweep",
                direction="LONG",
                stop_hint=wick,
                reasons=[
                    "вынос ликвидности под уровнем: пробой фитилём и возврат закрытием",
                    "ложный пробой — продажи под уровнем выкуплены",
                ],
            )
    return None


def detect_range_reversion(
    views: list[TimeframeView],
    price: float,
    regime: RegimeSnapshot,
    orderflow: OrderFlowSnapshot,
) -> ScenarioCandidate | None:
    """Mean-reversion в диапазоне: у границы + RSI-состояние + стакан.

    Только в RANGING/LOW_VOLATILITY. У поддержки (RSI перепродан, заявки
    покупателей плотнее) — LONG; у сопротивления — SHORT. Стоп за границей.
    Размер позиции ограничивается обычным риск-движком.
    """
    if not views or regime.regime not in ("RANGING", "LOW_VOLATILITY") or price <= 0:
        return None
    entry = views[0]
    atr = entry.atr if entry.atr > 0 else price * 0.01
    edge = 0.5 * atr

    support = entry.support
    resistance = entry.resistance
    if support is not None and support < price and abs(price - support) <= edge:
        if entry.rsi <= 42 and orderflow.imbalance > 0.1:
            return ScenarioCandidate(
                kind="range_reversion",
                direction="LONG",
                stop_hint=support - 0.3 * atr,
                reasons=[
                    "цена у нижней границы диапазона, продажи истощаются",
                    "стакан: заявок на покупку заметно больше, чем на продажу",
                ],
            )
    if resistance is not None and resistance > price and abs(resistance - price) <= edge:
        if entry.rsi >= 58 and orderflow.imbalance < -0.1:
            return ScenarioCandidate(
                kind="range_reversion",
                direction="SHORT",
                stop_hint=resistance + 0.3 * atr,
                reasons=[
                    "цена у верхней границы диапазона, покупки истощаются",
                    "стакан: заявок на продажу заметно больше, чем на покупку",
                ],
            )
    return None


def detect_breakout_watch(
    views: list[TimeframeView],
    price: float,
) -> ScenarioCandidate | None:
    """Условный сетап при сжатии: «вход при закрытии свечи выше/ниже X».

    Направление задаёт старший ТФ (честный контекстный уклон), сам вход — не
    рынок, а триггер. Выводится как condition в карточке, гейты не ослаблены.
    """
    if not views or price <= 0:
        return None
    entry = views[0]
    if not entry.squeeze:
        return None
    slow = views[-1]
    bias = "LONG" if slow.trend == "up" else "SHORT" if slow.trend == "down" else None
    if bias == "LONG" and entry.resistance is not None and entry.resistance > price:
        return ScenarioCandidate(
            kind="breakout_watch",
            direction="LONG",
            condition=f"вход только при закрытии свечи выше {entry.resistance:.8g} (пробой)",
            reasons=[
                "длительное сжатие волатильности — готовится выход из диапазона",
                "старший ТФ поддерживает лонг, ждём подтверждения пробоем",
            ],
        )
    if bias == "SHORT" and entry.support is not None and 0 < entry.support < price:
        return ScenarioCandidate(
            kind="breakout_watch",
            direction="SHORT",
            condition=f"вход только при закрытии свечи ниже {entry.support:.8g} (пробой)",
            reasons=[
                "длительное сжатие волатильности — готовится выход из диапазона",
                "старший ТФ поддерживает шорт, ждём подтверждения пробоем",
            ],
        )
    return None


def pick_scenario(
    views: list[TimeframeView],
    df_entry: pd.DataFrame | None,
    price: float,
    regime: RegimeSnapshot,
    orderflow: OrderFlowSnapshot,
    cfg: SignalConfig,
) -> ScenarioCandidate | None:
    """Выбрать лучший альтернативный сценарий (приоритет: sweep > choch > range > watch)."""
    for detector in (
        lambda: detect_liquidity_sweep(df_entry, views[0] if views else None),
        lambda: detect_choch_reversal(views, df_entry, regime),
        lambda: detect_range_reversion(views, price, regime, orderflow),
        lambda: detect_breakout_watch(views, price),
    ):
        candidate = detector()
        if candidate is not None:
            return candidate
    return None
