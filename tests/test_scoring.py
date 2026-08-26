"""Тесты скоринга скрытых монет."""

from src.analysis.fib import best_rr, compute_rr, fib_levels
from src.analysis.scoring import score_hidden_gem, tier_from_score, verdict_from_score


def test_score_bounds():
    b = score_hidden_gem(
        price_24h_pct=15.0, turnover_usd=5e7, volume_z=2.5, atr_pctl=0.7,
        rsi=60.0, roc_20=20.0, market_cap=4e8, st_dir=1, squeeze=True,
        funding_rate=0.0001, is_major=False,
    )
    assert 0 <= b.total <= 100
    assert abs(sum(b.parts.values()) - b.total) < 0.2


def test_major_penalty():
    b_major = score_hidden_gem(
        price_24h_pct=15.0, turnover_usd=5e7, volume_z=2.5, atr_pctl=0.7,
        rsi=60.0, roc_20=20.0, market_cap=4e8, st_dir=1, squeeze=True,
        funding_rate=0.0001, is_major=True,
    )
    b_minor = score_hidden_gem(
        price_24h_pct=15.0, turnover_usd=5e7, volume_z=2.5, atr_pctl=0.7,
        rsi=60.0, roc_20=20.0, market_cap=4e8, st_dir=1, squeeze=True,
        funding_rate=0.0001, is_major=False,
    )
    assert b_minor.total > b_major.total


def test_tiers_and_verdicts():
    assert tier_from_score(85) == "A+"
    assert tier_from_score(70) == "A"
    assert tier_from_score(60) == "B"
    action, _ = verdict_from_score(82)
    assert action == "STRONG_BUY"
    action, _ = verdict_from_score(20)
    assert action == "AVOID"


def test_fib_and_rr():
    fib = fib_levels(100, 200, direction=1)
    assert abs(fib.retracements[0.618] - (200 - 100 * 0.618)) < 1e-9
    assert fib.extensions[1.618] > 200
    rr = compute_rr(entry=150, stop=140, target=180, direction=1)
    assert abs(rr - 3.0) < 1e-9
    best, price = best_rr(150, 140, [160, 180, 200], 1)
    assert abs(best - 5.0) < 1e-9
    assert abs(price - 200) < 1e-9
