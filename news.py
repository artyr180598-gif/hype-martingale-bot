"""
Модуль новин для BlackHorn Capital.
Використовує безкоштовний CryptoCompare API.
Якщо ключ не налаштований — просто повертає порожній список.
"""
import os
import requests
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Безкоштовний ключ CryptoCompare (необов'язковий)
CC_API_KEY = os.getenv("CRYPTOCOMPARE_API_KEY", "")
CC_URL     = "https://min-api.cryptocompare.com/data/v2/news/"

# Ключові слова для фільтрації важливих новин
IMPORTANT_KEYWORDS = [
    "hyperliquid", "hype", "hack", "exploit",
    "crash", "ban", "sec", "regulation",
    "liquidation", "whale", "dump", "collapse"
]


def news_available() -> bool:
    """Перевірити чи доступний модуль новин."""
    return True   # завжди доступний, просто може повернути []


def fetch_news(
    limit: int = 5,
    important_only: bool = False
) -> list:
    """
    Отримати новини по HYPE/Hyperliquid.
    Повертає список dict: {id, title, url, source, published}
    """
    try:
        params = {
            "categories": "HYPE,Hyperliquid,DeFi",
            "excludeCategories": "Sponsored",
            "lang": "EN",
            "sortOrder": "latest",
        }
        if CC_API_KEY:
            params["api_key"] = CC_API_KEY

        r = requests.get(CC_URL, params=params, timeout=10)
        if r.status_code != 200:
            return []

        data = r.json().get("Data", [])
        posts = []
        for item in data[:limit * 3]:
            title = item.get("title", "")
            body  = item.get("body", "").lower()

            # Фільтр важливих
            if important_only:
                text_lower = title.lower() + " " + body
                if not any(kw in text_lower for kw in IMPORTANT_KEYWORDS):
                    continue

            # Час публікації
            ts = item.get("published_on", 0)
            if ts:
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                pub = dt.strftime("%d.%m %H:%M")
            else:
                pub = ""

            posts.append({
                "id":        item.get("id"),
                "title":     title,
                "url":       item.get("url", ""),
                "source":    item.get("source_info", {}).get("name", ""),
                "published": pub,
                "tags":      item.get("tags", "").lower()
            })

            if len(posts) >= limit:
                break

        return posts

    except Exception as e:
        logger.error(f"fetch_news: {e}")
        return []


def sentiment(posts: list) -> tuple[str, str]:
    """
    Простий аналіз настрою за ключовими словами.
    Повертає (emoji, label).
    """
    if not posts:
        return "😐", "Нейтральний"

    negative_kw = [
        "crash", "dump", "hack", "exploit", "ban",
        "liquidation", "collapse", "fall", "drop",
        "fear", "panic", "sell", "bear", "down"
    ]
    positive_kw = [
        "surge", "rally", "pump", "gain", "rise",
        "bull", "up", "growth", "adoption", "launch",
        "partnership", "record", "high", "win"
    ]

    neg_count = 0
    pos_count = 0

    for p in posts:
        text = (p.get("title", "") + " " + p.get("tags", "")).lower()
        neg_count += sum(1 for kw in negative_kw if kw in text)
        pos_count += sum(1 for kw in positive_kw if kw in text)

    if pos_count > neg_count * 1.5:
        return "🟢", "Позитивний"
    elif neg_count > pos_count * 1.5:
        return "🔴", "Негативний"
    else:
        return "🟡", "Нейтральний"
