"""
Общие фикстуры тестов v2.

Все тесты работают на DemoProvider (офлайн, детерминированно), поэтому
набор воспроизводится без сети и без ключей API.
"""

from __future__ import annotations

import pytest

from v2.config import V2Config
from v2.data.demo import DemoProvider


@pytest.fixture()
def config() -> V2Config:
    """Конфиг с явно заданными порогами (не зависит от .env разработчика)."""
    cfg = V2Config(
        DATA_MODE="demo",
        LOG_LEVEL="WARNING",
        SCAN_L1_ENABLED=True,
        SCAN_L2_ENABLED=True,
        SCAN_L3_ENABLED=True,
        L1_MIN_VOLUME_5M_USD=500_000.0,
        L1_MIN_TX_5M=100,
        L2_MAX_TOP10_PCT=40.0,
        L2_MIN_LP_LOCKED_PCT=80.0,
        L2_MIN_LP_LOCK_DAYS=180,
        L2_BLOCK_IF_MINTABLE=True,
        L2_BLOCK_IF_BLACKLIST=True,
        L3_MIN_DEPLOYER_AGE_DAYS=7,
        ATR_SL_MULTIPLIER=1.8,
        ATR_TP_MULTIPLIER=3.6,
        MIN_RISK_REWARD=2.0,
        RISK_PER_TRADE_PCT=1.0,
        MAX_POSITION_PCT=10.0,
        DEFAULT_DEPOSIT_USD=1000.0,
        AI_ENABLED=False,
        OPENAI_API_KEY="",
        X_BEARER_TOKEN="",
        EXECUTOR_MODE="paper",
        EXECUTOR_JOURNAL_PATH="./data/test_v2_orders.jsonl",
    )
    return cfg


@pytest.fixture()
def provider(config) -> DemoProvider:
    return DemoProvider(config)


@pytest.fixture()
def token_by_symbol(provider):
    async def _get(symbol: str):
        found = await provider.resolve_token(symbol)
        assert found, f"в демо-вселенной нет {symbol}"
        return found[0]

    return _get
