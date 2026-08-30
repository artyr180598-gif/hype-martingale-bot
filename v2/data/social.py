"""
Социальный фон: X (Twitter) API v2 + эмуляция хайпа без ключа.

Режимы:
  1. X_BEARER_TOKEN задан → реальный поиск твитов за последние N часов
     (recent search), считаем упоминания, уникальных авторов, сентимент по
     словарю и метрики вовлечённости.
  2. Ключа нет → StubSocial: оценка хайпа по рыночным прокси-метрикам самого
     токена (ускорение объёма, доля покупок, изменение цены, возраст пула).
     Это НЕ данные из соцсетей, и в отчёте они помечены is_stub=True с
     явной подписью «эмуляция».

Так бот остаётся полезным без платного API, но не врёт пользователю: заглушка
всегда видна в отчёте.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

from v2.config import V2Config
from v2.core.errors import ProviderUnavailable
from v2.core.logging import get_logger
from v2.core.monitor import monitor
from v2.data.provider import MarketProvider
from v2.models import SocialReport, TokenCandidate

logger = get_logger("data.social")

X_SEARCH = "https://api.twitter.com/2/tweets/search/recent"

POSITIVE = {
    "bullish", "moon", "pump", "buy", "long", "breakout", "partnership", "listing",
    "airdrop", "accumulating", "ath", "gem", "сокровище", "покупай", "растёт", "листинг",
}
NEGATIVE = {
    "scam", "rug", "dump", "sell", "short", "honeypot", "exploit", "hack", "dead",
    "exit", "мошенники", "скам", "слив", "падение",
}


def _sentiment(text: str) -> float:
    """Простейший лексический сентимент: доля позитива минус доля негатива."""
    words = [w.strip(".,!?():;\"'").lower() for w in text.split() if w.strip(".,!?():;\"'")]
    if not words:
        return 0.0
    pos = sum(1 for w in words if w in POSITIVE)
    neg = sum(1 for w in words if w in NEGATIVE)
    return (pos - neg) / len(words) * 3.0


class SocialProvider(MarketProvider):
    name = "social"

    def __init__(self, config: V2Config, http) -> None:
        self.config = config
        self.http = http

    async def social(self, token: TokenCandidate, window_hours: int = 2) -> SocialReport | None:
        if self.config.X_BEARER_TOKEN:
            try:
                return await self._from_x(token, window_hours)
            except ProviderUnavailable as exc:
                monitor.record("data.social.x", exc)
                logger.warning("X API недоступен — переходим на эмуляцию хайпа")
        return self._stub(token, window_hours)

    async def _from_x(self, token: TokenCandidate, window_hours: int) -> SocialReport:
        query = f"(${token.symbol} OR {token.name}) -is:retweet lang:en"
        payload = await self.http.get_json(
            X_SEARCH,
            params={
                "query": query[:512],
                "max_results": 100,
                "tweet.fields": "created_at,public_metrics,author_id",
            },
            headers={"Authorization": f"Bearer {self.config.X_BEARER_TOKEN}"},
            component="data.social.x",
        )
        tweets = (payload or {}).get("data") or []
        meta = (payload or {}).get("meta") or {}
        if not tweets:
            return SocialReport(
                window_hours=window_hours,
                mentions=int(meta.get("result_count") or 0),
                hype_score=0.0,
                sentiment=0.0,
                top_posts=[],
                source="x-api",
                is_stub=False,
            )

        sentiments = [_sentiment(t.get("text") or "") for t in tweets]
        authors = {t.get("author_id") for t in tweets if t.get("author_id")}
        engagement = 0
        for tweet in tweets:
            metrics = tweet.get("public_metrics") or {}
            engagement += int(metrics.get("like_count") or 0) + 2 * int(metrics.get("retweet_count") or 0)

        mentions = len(tweets)
        # хайп = логарифм упоминаний + бонус за вовлечённость (0..100)
        hype = min(100.0, 18 * math.log10(mentions + 1) * 10 + min(40.0, engagement / 50))
        top = sorted(
            tweets,
            key=lambda t: (t.get("public_metrics") or {}).get("like_count") or 0,
            reverse=True,
        )[:3]
        return SocialReport(
            window_hours=window_hours,
            mentions=mentions,
            unique_authors=len(authors),
            hype_score=round(hype, 1),
            sentiment=round(max(-1.0, min(1.0, sum(sentiments) / len(sentiments))), 2),
            top_posts=[str(t.get("text") or "")[:180] for t in top],
            keywords=[token.symbol.lower()],
            source="x-api",
            is_stub=False,
        )

    # ── эмуляция ─────────────────────────────────────────────────
    def _stub(self, token: TokenCandidate, window_hours: int) -> SocialReport:
        """
        Оценка хайпа по рыночным прокси.

        Логика: нормальное отношение 5-минутного оборота к среднему — около
        1/288 суточного. Превышение в 3–10 раз означает всплеск внимания,
        который в 90% случаев сопровождается ростом упоминаний в соцсетях.
        Доля покупок и скорость изменения цены корректируют оценку.
        """
        expected_5m = token.volume_24h_usd / 288.0
        ratio = token.volume_5m_usd / expected_5m if expected_5m > 0 else 1.0
        hype = 20 + 26 * math.log10(max(ratio, 0.1))
        hype += min(18.0, token.buy_ratio_5m * 20)          # перевес покупок
        hype += min(22.0, max(0.0, token.price_change_1h_pct) * 1.6)  # импульс цены
        if token.age_hours < 72:
            hype += 12.0                                    # свежие пулы обсуждают активнее
        hype = max(1.0, min(100.0, hype))

        # детерминированные «упоминания», согласованные с хайпом
        seed = int(hashlib.sha256(token.address.encode()).hexdigest()[:8], 16)
        mentions = int(hype * (6 + seed % 9) * math.sqrt(max(window_hours, 1) / 2))
        sentiment = round(max(-1.0, min(1.0, (token.buy_ratio_5m - 0.5) * 2 + token.price_change_1h_pct / 40)), 2)

        return SocialReport(
            window_hours=window_hours,
            mentions=mentions,
            unique_authors=int(mentions * 0.7),
            hype_score=round(hype, 1),
            sentiment=sentiment,
            top_posts=[
                f"[эмуляция] ускорение объёма x{ratio:.1f} относительно суточной нормы",
                f"[эмуляция] доля покупок за 5 минут {token.buy_ratio_5m * 100:.0f}%",
                f"[эмуляция] изменение цены за час {token.price_change_1h_pct:+.1f}%",
            ],
            keywords=[token.symbol.lower()],
            source="emulated",
            is_stub=True,
        )

    @staticmethod
    def merge_ai(report: SocialReport, ai_notes: str, ai_verdict: str) -> SocialReport:
        """Дополняет отчёт выводом AI-модуля (если он доступен)."""
        report.ai_notes = ai_notes
        if ai_verdict:
            report.keywords = list({*report.keywords, f"ai:{ai_verdict}"})
        return report
