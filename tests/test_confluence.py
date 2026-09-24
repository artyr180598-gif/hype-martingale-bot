from src.strategies.confluence import ConfluenceAnalyzer


def test_atr_is_positive():
    candles = [
        {'high': 110, 'low': 100, 'close': 105},
        {'high': 115, 'low': 104, 'close': 112},
        {'high': 118, 'low': 108, 'close': 116},
    ]
    assert ConfluenceAnalyzer._atr(candles) > 0
