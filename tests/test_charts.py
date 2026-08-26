"""Тесты рендера графиков."""

import pytest

from src.charts.renderer import chart_analysis, chart_gem_overview
from src.data.indicators import compute_all


@pytest.mark.asyncio
async def test_chart_analysis_png(settings, sample_df):
    df = compute_all(await sample_df("SOLUSDT", "15m", 200))
    path = settings.chart_dir / "test_sol.png"
    out = chart_analysis(df, result=None, path=path)
    assert out.exists()
    assert out.stat().st_size > 10_000
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_chart_gem_overview_png(settings):
    gems = [
        {"symbol": "PEPEUSDT", "score": 88.0, "price_24h_pct": 22.0, "direction": "LONG"},
        {"symbol": "NOVAUSDT", "score": 74.0, "price_24h_pct": -9.0, "direction": "SHORT"},
    ]
    path = settings.chart_dir / "gems_test.png"
    out = chart_gem_overview(gems, path)
    assert out.exists()
    assert out.stat().st_size > 5_000
