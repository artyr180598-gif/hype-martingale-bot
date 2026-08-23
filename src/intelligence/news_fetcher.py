"""
Asynchronous News Ingestion and Aggregator.
"""
from typing import Any

import aiohttp

from src.config.settings import settings
from src.core.logging import get_logger
from src.intelligence.sentiment_analyzer import SentimentAnalyzer

logger = get_logger("intelligence.news")


class NewsFetcher:
    """
    Ingests live breaking crypto headlines and runs sentiment parsing.
    """

    CRYPTOCOMPARE_URL = "https://min-api.cryptocompare.com/data/v2/news/"

    @classmethod
    async def fetch_latest_news(cls, limit: int = 10, category: str = "BTC,ETH,DeFi") -> list[dict[str, Any]]:
        params = {
            "categories": category,
            "excludeCategories": "Sponsored",
            "lang": "EN",
            "sortOrder": "latest",
        }
        if settings.CRYPTOCOMPARE_API_KEY:
            params["api_key"] = settings.CRYPTOCOMPARE_API_KEY

        articles: list[dict[str, Any]] = []

        try:
            timeout = aiohttp.ClientTimeout(total=8.0)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(cls.CRYPTOCOMPARE_URL, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        raw_list = data.get("Data", [])[:limit]
                        for item in raw_list:
                            title = item.get("title", "")
                            body = item.get("body", "")
                            full_text = f"{title}. {body}"
                            sent_res = SentimentAnalyzer.analyze_text(full_text)

                            articles.append({
                                "source_id": str(item.get("id", "")),
                                "source": item.get("source_info", {}).get("name", "CryptoNews"),
                                "title": title,
                                "url": item.get("url", ""),
                                "published_at_ms": int(item.get("published_on", 0)) * 1000,
                                "sentiment": sent_res.sentiment.value,
                                "sentiment_score": sent_res.score,
                                "impact": sent_res.impact.value,
                                "tags": item.get("tags", "").split("|"),
                            })
        except Exception as e:
            logger.debug("Live news fetch skipped or errored", error=str(e))

        return articles
