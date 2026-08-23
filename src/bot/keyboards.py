"""
Telegram Inline Keyboard Layouts and Navigation Menus.
"""
from typing import Any


class BotKeyboards:
    """
    Standard interactive inline keyboards.
    """

    @staticmethod
    def main_menu() -> dict[str, Any]:
        return {
            "inline_keyboard": [
                [
                    {"text": "📊 МАРКЕТ", "callback_data": "menu:market"},
                    {"text": "🔥 ТОП СЕТАПЫ", "callback_data": "menu:top"},
                ],
                [
                    {"text": "₿ BTC АНАЛИЗ", "callback_data": "analyze:BTCUSDT"},
                    {"text": "Ξ ETH АНАЛИЗ", "callback_data": "analyze:ETHUSDT"},
                ],
                [
                    {"text": "🧪 БЭКТЕСТ", "callback_data": "menu:backtest"},
                    {"text": "🎮 PAPER ТРЕЙДИНГ", "callback_data": "menu:paper"},
                ],
                [
                    {"text": "🧠 СТРАТЕГИИ", "callback_data": "menu:strategies"},
                    {"text": "📰 НОВОСТИ", "callback_data": "menu:news"},
                ],
                [
                    {"text": "🔔 АЛЕРТЫ", "callback_data": "menu:alerts"},
                    {"text": "⚙️ НАСТРОЙКИ", "callback_data": "menu:settings"},
                ],
            ]
        }

    @staticmethod
    def signal_detail_keyboard(symbol: str) -> dict[str, Any]:
        return {
            "inline_keyboard": [
                [
                    {"text": "📊 Полный анализ", "callback_data": f"analyze:{symbol}"},
                    {"text": "🧪 Тест сетапа", "callback_data": f"bt:{symbol}"},
                ],
                [
                    {"text": "🎮 Открыть Paper Trade", "callback_data": f"paper_open:{symbol}"},
                    {"text": "🔔 Подписаться", "callback_data": f"alert_sub:{symbol}"},
                ],
                [
                    {"text": "🔙 Главное меню", "callback_data": "menu:main"},
                ],
            ]
        }

    @staticmethod
    def backtest_symbols_keyboard() -> dict[str, Any]:
        return {
            "inline_keyboard": [
                [
                    {"text": "BTC (15m)", "callback_data": "run_bt:BTCUSDT:15m"},
                    {"text": "ETH (15m)", "callback_data": "run_bt:ETHUSDT:15m"},
                ],
                [
                    {"text": "SOL (15m)", "callback_data": "run_bt:SOLUSDT:15m"},
                    {"text": "XRP (15m)", "callback_data": "run_bt:XRPUSDT:15m"},
                ],
                [
                    {"text": "🔙 Главное меню", "callback_data": "menu:main"},
                ],
            ]
        }

    @staticmethod
    def settings_risk_keyboard() -> dict[str, Any]:
        return {
            "inline_keyboard": [
                [
                    {"text": "🛡 Консервативный (0.75%)", "callback_data": "set_risk:CONSERVATIVE"},
                ],
                [
                    {"text": "⚖️ Сбалансированный (1.5%)", "callback_data": "set_risk:BALANCED"},
                ],
                [
                    {"text": "🚀 Агрессивный (2.5%)", "callback_data": "set_risk:AGGRESSIVE"},
                ],
                [
                    {"text": "🔙 Главное меню", "callback_data": "menu:main"},
                ],
            ]
        }

    @staticmethod
    def back_to_main_keyboard() -> dict[str, Any]:
        return {
            "inline_keyboard": [
                [{"text": "🔙 Главное меню", "callback_data": "menu:main"}]
            ]
        }
