from __future__ import annotations

from dataclasses import dataclass, asdict
import json
from pathlib import Path

SETTINGS_PATH = Path("data/confirmation_settings.json")


@dataclass
class OptionalConfirmationSettings:
    """User-selectable confirmation filters.

    All optional filters are disabled by default so enabling this module cannot
    silently change the existing Pump/Dump scanner behaviour.
    """

    order_flow: bool = False
    order_flow_min_delta_pct: float = 10.0
    orderbook: bool = False
    orderbook_min_bid_pct: float = 52.0
    orderbook_persistence: bool = True
    open_interest: bool = False
    oi_min_change_pct: float = 0.5
    volume: bool = False
    volume_ratio: float = 1.35
    structure: bool = False
    context_15m: bool = False
    impulse_decay: bool = False
    funding: bool = False

    @classmethod
    def load(cls) -> "OptionalConfirmationSettings":
        try:
            raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            values = asdict(cls())
            values.update(raw)
            return cls(**values)
        except Exception:
            return cls()

    def save(self) -> None:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def reset(self) -> None:
        fresh = type(self)()
        self.__dict__.update(fresh.__dict__)
        self.save()


DESCRIPTIONS = {
    "order_flow": "Показывает, кто агрессивнее исполняет сделки — покупатели или продавцы.",
    "orderbook": "Проверяет реальное соотношение ликвидности покупателей и продавцов в стакане и его устойчивость.",
    "open_interest": "Показывает, растёт ли открытый интерес во фьючерсах вместе с движением.",
    "volume": "Сравнивает текущий объём с обычным объёмом и ищет подтверждение активности.",
    "structure": "Проверяет локальную структуру цены: пробой, новые максимумы/минимумы и удержание движения.",
    "context_15m": "Даёт более широкий 15-минутный контекст, но сам по себе не блокирует ранний сигнал.",
    "impulse_decay": "Ищет признаки выдохшего импульса: цена ещё движется, а объём/поток уже ослабевают.",
    "funding": "Показывает ставку финансирования как дополнительный контекст позиционирования.",
}


def menu_text(settings: OptionalConfirmationSettings) -> str:
    def mark(value: bool) -> str:
        return "🟢 ВКЛ" if value else "⚪ ВЫКЛ"

    return (
        "➕ Дополнительные подтверждения\n\n"
        "Это необязательные фильтры. По умолчанию они выключены, поэтому базовый поиск Pump/Dump не меняется.\n\n"
        f"1. 📊 Order Flow — {mark(settings.order_flow)}\n"
        f"   {DESCRIPTIONS['order_flow']}\n\n"
        f"2. 📚 Стакан — {mark(settings.orderbook)}\n"
        f"   {DESCRIPTIONS['orderbook']}\n\n"
        f"3. 📈 Open Interest — {mark(settings.open_interest)}\n"
        f"   {DESCRIPTIONS['open_interest']}\n\n"
        f"4. 📦 Объём — {mark(settings.volume)}\n"
        f"   {DESCRIPTIONS['volume']}\n\n"
        f"5. 📐 Структура — {mark(settings.structure)}\n"
        f"   {DESCRIPTIONS['structure']}\n\n"
        f"6. 🕐 Контекст 15m — {mark(settings.context_15m)}\n"
        f"   {DESCRIPTIONS['context_15m']}\n\n"
        f"7. 💨 Выдох импульса — {mark(settings.impulse_decay)}\n"
        f"   {DESCRIPTIONS['impulse_decay']}\n\n"
        f"8. 💰 Funding — {mark(settings.funding)}\n"
        f"   {DESCRIPTIONS['funding']}\n\n"
        "Пороговые значения можно менять отдельно. Они не являются гарантией движения."
    )
