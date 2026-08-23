"""
News and Event Signal Fusion Layer.
"""
from typing import Any

from src.config.constants import SignalDirection
from src.signals.models import SignalSetup


class NewsSignalFusion:
    """
    Dynamically adjusts signal scores and injects event warnings when major news breaks.
    """

    @classmethod
    def apply_news_fusion(cls, setup: SignalSetup, recent_articles: list[dict[str, Any]]) -> SignalSetup:
        if not recent_articles or setup.direction == SignalDirection.NO_TRADE:
            return setup

        symbol_prefix = setup.symbol.replace("USDT", "").replace("USDC", "").lower()

        # Find relevant news for this symbol
        relevant_news = []
        for a in recent_articles:
            title = a.get("title", "").lower()
            tags = [t.lower() for t in a.get("tags", [])]
            if symbol_prefix in title or symbol_prefix in tags or "btc" in title or "crypto" in title:
                relevant_news.append(a)

        if not relevant_news:
            return setup

        latest_art = relevant_news[0]
        sentiment = latest_art.get("sentiment", "NEUTRAL")
        impact = latest_art.get("impact", "LOW")

        # Conflict check: Bullish signal + Critical Bearish News
        if setup.direction == SignalDirection.LONG and sentiment == "BEARISH" and impact in ("HIGH", "CRITICAL"):
            setup.score = max(50.0, setup.score - 20.0)
            setup.risk_factors.append(f"Breaking bearish headline: '{latest_art['title']}' ({impact} Impact)")
            if setup.score < 60.0:
                setup.direction = SignalDirection.NO_TRADE

        elif setup.direction == SignalDirection.SHORT and sentiment == "BULLISH" and impact in ("HIGH", "CRITICAL"):
            setup.score = max(50.0, setup.score - 20.0)
            setup.risk_factors.append(f"Breaking bullish headline: '{latest_art['title']}' ({impact} Impact)")
            if setup.score < 60.0:
                setup.direction = SignalDirection.NO_TRADE

        return setup
