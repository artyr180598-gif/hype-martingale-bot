from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📊 MARKET", callback_data="market"), InlineKeyboardButton(text="🔥 SIGNALS", callback_data="signals")],
        [InlineKeyboardButton(text="₿ BTC", callback_data="asset:BTCUSDT"), InlineKeyboardButton(text="Ξ ETH", callback_data="asset:ETHUSDT")],
        [InlineKeyboardButton(text="🚀 TOP SETUPS", callback_data="signals"), InlineKeyboardButton(text="⚡ VOLATILITY", callback_data="volatility")],
        [InlineKeyboardButton(text="💧 LIQUIDATIONS", callback_data="liquidations"), InlineKeyboardButton(text="📰 NEWS", callback_data="news")],
        [InlineKeyboardButton(text="🌎 MARKET OVERVIEW", callback_data="market"), InlineKeyboardButton(text="📈 WATCHLIST", callback_data="watchlist")],
        [InlineKeyboardButton(text="🔔 ALERTS", callback_data="alerts"), InlineKeyboardButton(text="⚙️ SETTINGS", callback_data="settings")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def analysis_menu(symbol: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Full Analysis", callback_data=f"analysis:{symbol}")],
            [InlineKeyboardButton(text="📈 Chart", callback_data=f"chart:{symbol}"), InlineKeyboardButton(text="🧠 Why?", callback_data=f"why:{symbol}")],
            [InlineKeyboardButton(text="⚠ Risks", callback_data=f"risk:{symbol}"), InlineKeyboardButton(text="🔔 Alert", callback_data=f"alert:{symbol}")],
            [InlineKeyboardButton(text="⬅️ Main menu", callback_data="home")],
        ]
    )
