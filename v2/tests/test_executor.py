"""Исполнитель: претрейд-валидация, журнал, сопровождение по стопам/целям."""

from __future__ import annotations

import json
import pathlib

import pytest

from v2.core.errors import ConfigError, RiskRejected
from v2.engine import AnalysisEngine
from v2.executor import Executor
from v2.models import CoinReport, TradePlan


def _report(direction="LONG", rr=2.5, verdict="ENTER", risk=3, slippage=0.2) -> CoinReport:
    report = CoinReport(token=_token())
    report.plan = TradePlan(
        direction=direction, entry=100.0, stop_loss=96.0, targets=[110.0],
        rr=rr, qty=10.0, position_usd=1000.0, position_pct=5.0, risk_usd=40.0, leverage=2,
    )
    report.verdict = verdict
    report.risk_score = risk
    report.micro.slippage_pct = slippage
    report.micro.grade = "ok"
    return report


def _token():
    from v2.models import TokenCandidate

    return TokenCandidate(chain="ethereum", address="0xtitan", symbol="TITAN", price_usd=100.0)


async def test_rejects_order_on_avoid_verdict(config):
    executor = Executor(config)
    with pytest.raises(RiskRejected):
        await executor.open_position(_report(verdict="AVOID"))


async def test_rejects_order_on_low_rr(config):
    executor = Executor(config)
    with pytest.raises(RiskRejected):
        await executor.open_position(_report(rr=1.2))


async def test_rejects_order_on_huge_slippage(config):
    executor = Executor(config)
    with pytest.raises(RiskRejected):
        await executor.open_position(_report(slippage=4.0))


async def test_dry_run_does_not_touch_journal(config, tmp_path):
    config.EXECUTOR_MODE = "dry_run"
    config.EXECUTOR_JOURNAL_PATH = tmp_path / "orders.jsonl"
    executor = Executor(config)
    receipt = await executor.open_position(_report())
    assert receipt.status == "simulated"
    assert not pathlib.Path(config.EXECUTOR_JOURNAL_PATH).exists()


async def test_paper_order_written_to_journal(config, tmp_path):
    config.EXECUTOR_JOURNAL_PATH = tmp_path / "orders.jsonl"
    executor = Executor(config)
    receipt = await executor.open_position(_report())
    assert receipt.status == "filled"
    lines = pathlib.Path(config.EXECUTOR_JOURNAL_PATH).read_text(encoding="utf-8").strip().splitlines()
    assert json.loads(lines[0])["event"] == "open"
    assert json.loads(lines[0])["symbol"] == "TITAN"


async def test_position_closed_on_stop_loss(config, tmp_path):
    config.EXECUTOR_JOURNAL_PATH = tmp_path / "orders.jsonl"
    executor = Executor(config)
    await executor.open_position(_report())
    closed = await executor.update("TITAN", 95.0)
    assert len(closed) == 1
    assert closed[0].reason == "стоп-лосс"
    assert closed[0].pnl_usd == pytest.approx(-40.0)   # (96−100)×10
    assert executor.stats()["closed"] == 1


async def test_position_closed_on_target(config, tmp_path):
    config.EXECUTOR_JOURNAL_PATH = tmp_path / "orders.jsonl"
    executor = Executor(config)
    await executor.open_position(_report())
    closed = await executor.update("TITAN", 110.0)
    assert closed[0].reason == "цель 1"
    assert closed[0].pnl_usd == pytest.approx(100.0)   # (110−100)×10


async def test_short_position_pnl_sign(config, tmp_path):
    """У шорта цель ниже входа, стоп выше — иначе уровни бессмысленны."""
    config.EXECUTOR_JOURNAL_PATH = tmp_path / "orders.jsonl"
    executor = Executor(config)
    report = _report(direction="SHORT")
    report.plan.stop_loss = 104.0
    report.plan.targets = [90.0]
    await executor.open_position(report)
    closed = await executor.update("TITAN", 90.0)
    assert closed[0].pnl_usd == pytest.approx(100.0)   # (100−90)×10
    assert closed[0].reason == "цель 1"


async def test_live_mode_requires_explicit_flag(config):
    config.EXECUTOR_MODE = "live"
    config.EXECUTOR_ALLOW_LIVE = False
    executor = Executor(config)
    with pytest.raises(ConfigError):
        await executor.open_position(_report())


async def test_validate_lists_all_problems(config):
    executor = Executor(config)
    issues = executor.validate(_report(verdict="AVOID", rr=0.5, slippage=9.0))
    assert len(issues) >= 3
