"""
Crypto and Macroeconomic News Sentiment Analyzer.
"""
from dataclasses import dataclass

from src.config.constants import NewsImpact, NewsSentiment


@dataclass
class SentimentResult:
    sentiment: NewsSentiment
    score: float           # -1.0 to +1.0
    impact: NewsImpact
    detected_keywords: list[str]


class SentimentAnalyzer:
    """
    High-speed deterministic NLP and domain lexicon sentiment analyzer.
    """

    BULLISH_KEYWORDS = [
        "surge", "rally", "pump", "adoption", "partnership", "approved", "etf approval",
        "institutional inflow", "record high", "bullish", "upgrade", "mainnet launch",
        "burn", "buyback", "staking reward", "rate cut", "treasury reserve",
    ]

    BEARISH_KEYWORDS = [
        "crash", "dump", "hack", "exploit", "sec lawsuit", "investigation", "ban",
        "subpoena", "liquidation cascade", "insolvent", "bankrupt", "freeze",
        "stolen", "breach", "outage", "delisting", "token unlock", "rate hike",
    ]

    CRITICAL_KEYWORDS = [
        "hack", "exploit", "insolvent", "bankrupt", "sec ban", "cpi", "fomc", "emergency"
    ]

    @classmethod
    def analyze_text(cls, text: str) -> SentimentResult:
        t_lower = text.lower()

        bull_hits = [kw for kw in cls.BULLISH_KEYWORDS if kw in t_lower]
        bear_hits = [kw for kw in cls.BEARISH_KEYWORDS if kw in t_lower]
        crit_hits = [kw for kw in cls.CRITICAL_KEYWORDS if kw in t_lower]

        b_count = len(bull_hits)
        s_count = len(bear_hits)

        # Sentiment classification
        if b_count > s_count * 1.5:
            sentiment = NewsSentiment.BULLISH
            score = min(1.0, 0.3 + (b_count * 0.2))
        elif s_count > b_count * 1.5:
            sentiment = NewsSentiment.BEARISH
            score = max(-1.0, -0.3 - (s_count * 0.2))
        else:
            sentiment = NewsSentiment.NEUTRAL
            score = 0.0

        # Impact level
        if crit_hits or (b_count + s_count >= 4):
            impact = NewsImpact.CRITICAL if crit_hits else NewsImpact.HIGH
        elif b_count + s_count >= 2:
            impact = NewsImpact.MEDIUM
        else:
            impact = NewsImpact.LOW

        return SentimentResult(
            sentiment=sentiment,
            score=round(score, 2),
            impact=impact,
            detected_keywords=bull_hits + bear_hits,
        )
