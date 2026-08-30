"""
Трёхуровневый сканер: каждый фильтр обязан реально отсекать.

Проверяем на демо-вселенной, где для каждого типа скама есть свой токен:
MOONX (mint), SAFER (LP не заблокирована), WHALE (концентрация холдеров),
REKT (blacklist + свежий деплоер), HONNY (honeypot), EMBER (деплоеру 2 дня),
GROVE/SLEEP (мало объёма — отсеиваются ещё на уровне 1).
"""

from __future__ import annotations

import pytest

from v2.data.demo import DemoProvider
from v2.models import ContractRisk, HolderStats, LpLockInfo, TokenCandidate
from v2.scanner.level1_quick import QuickScanner, heat_score
from v2.scanner.level2_deep import DeepScanner, SecurityEvaluator
from v2.scanner.level3_onchain import OnchainScanner, apply_deployer
from v2.scanner.pipeline import ScannerPipeline


def _token(symbol: str, **overrides) -> TokenCandidate:
    base = dict(
        chain="ethereum", address=f"0x{symbol}", symbol=symbol, name=symbol,
        quote_symbol="USDC", price_usd=1.0, volume_5m_usd=1_000_000.0,
        volume_24h_usd=100_000_000.0, tx_5m=500, buys_5m=300, sells_5m=200,
        liquidity_usd=2_000_000.0, market_cap_usd=20_000_000.0,
        pair_created_ms=1_700_000_000_000,
    )
    base.update(overrides)
    return TokenCandidate(**base)


# ═══════════════════════════════════════════════════════════════
#  УРОВЕНЬ 1
# ═══════════════════════════════════════════════════════════════
def test_l1_rejects_low_volume_5m(config):
    scanner = QuickScanner(config, DemoProvider(config))
    token = _token("LOWVOL", volume_5m_usd=120_000.0)
    assert "объём 5м" in scanner.reject_reason(token)


def test_l1_rejects_low_tx_count(config):
    """Оборот есть, а сделок мало — wash-трейдинг одной сделкой кита."""
    scanner = QuickScanner(config, DemoProvider(config))
    token = _token("FEWTX", volume_5m_usd=2_000_000.0, tx_5m=40)
    assert "транзакций за 5м" in scanner.reject_reason(token)


def test_l1_rejects_stablecoin_and_thin_pool(config):
    scanner = QuickScanner(config, DemoProvider(config))
    assert scanner.reject_reason(_token("USDC")) == "стейблкойн/обёртка (не актив)"
    thin = _token("THIN", liquidity_usd=12_000.0)
    assert "ликвидность" in scanner.reject_reason(thin)


def test_l1_accepts_healthy_token(config):
    scanner = QuickScanner(config, DemoProvider(config))
    assert scanner.reject_reason(_token("GOOD")) is None


async def test_l1_run_filters_demo_universe(config, provider):
    survivors, stage = await QuickScanner(config, provider).run(limit=50)
    assert stage.entered == 14
    assert 0 < stage.passed < stage.entered
    symbols = {t.symbol for t in survivors}
    assert "GROVE" not in symbols and "SLEEP" not in symbols   # мусор отсеян
    assert all(t.volume_5m_usd >= config.L1_MIN_VOLUME_5M_USD for t in survivors)
    assert all(t.tx_5m >= config.L1_MIN_TX_5M for t in survivors)


async def test_l1_disabled_passes_everything(config, provider):
    config.SCAN_L1_ENABLED = False
    survivors, stage = await QuickScanner(config, provider).run(limit=50)
    assert stage.passed == stage.entered == 14
    assert stage.degraded


def test_heat_score_prefers_acceleration():
    calm = _token("CALM", volume_5m_usd=300_000.0, volume_24h_usd=86_400_000.0)
    hot = _token("HOT", volume_5m_usd=3_000_000.0, volume_24h_usd=86_400_000.0)
    assert heat_score(hot) > heat_score(calm)


# ═══════════════════════════════════════════════════════════════
#  УРОВЕНЬ 2 — скам-фильтр
# ═══════════════════════════════════════════════════════════════
def _clean_checks() -> tuple[HolderStats, LpLockInfo, ContractRisk]:
    return (
        HolderStats(top1_pct=5.0, top10_pct=22.0, holders_count=5000),
        LpLockInfo(locked_pct=100.0, lock_days_left=365.0),
        ContractRisk(is_mintable=False, has_blacklist=False, is_honeypot=False,
                     buy_tax_pct=0.0, sell_tax_pct=0.0, source_verified=True),
    )


def test_l2_clean_token_passes(config):
    holders, lp, contract = _clean_checks()
    report = SecurityEvaluator(config).evaluate(_token("OK"), holders, lp, contract)
    assert not report.blocked
    assert report.score > 80
    assert report.passed


def test_l2_blocks_holder_concentration(config):
    holders, lp, contract = _clean_checks()
    holders.top10_pct = 47.0
    report = SecurityEvaluator(config).evaluate(_token("WHALE"), holders, lp, contract)
    assert report.blocked
    assert any("топ-10" in b.lower() or "топ-10" in b for b in report.blockers)


def test_l2_blocks_single_whale(config):
    holders, lp, contract = _clean_checks()
    holders.top1_pct = 31.0
    report = SecurityEvaluator(config).evaluate(_token("ONEWHALE"), holders, lp, contract)
    assert report.blocked


def test_l2_blocks_unlocked_lp(config):
    holders, lp, contract = _clean_checks()
    lp.locked_pct = 0.0
    report = SecurityEvaluator(config).evaluate(_token("NOLP"), holders, lp, contract)
    assert report.blocked
    assert any("LP" in b for b in report.blockers)


def test_l2_blocks_short_lp_lock(config):
    """LP заблокирована, но на месяц — это не защита от rug."""
    holders, lp, contract = _clean_checks()
    lp.locked_pct = 100.0
    lp.lock_days_left = 30.0
    report = SecurityEvaluator(config).evaluate(_token("SHORTLP"), holders, lp, contract)
    assert report.blocked


def test_l2_blocks_mint_and_blacklist(config):
    holders, lp, contract = _clean_checks()
    contract.is_mintable = True
    assert SecurityEvaluator(config).evaluate(_token("MINT"), holders, lp, contract).blocked

    holders, lp, contract = _clean_checks()
    contract.has_blacklist = True
    assert SecurityEvaluator(config).evaluate(_token("BLACK"), holders, lp, contract).blocked


def test_l2_blocks_honeypot(config):
    holders, lp, contract = _clean_checks()
    contract.is_honeypot = True
    report = SecurityEvaluator(config).evaluate(_token("HONEY"), holders, lp, contract)
    assert report.blocked
    assert report.score < 60


def test_l2_blocks_high_sell_tax(config):
    holders, lp, contract = _clean_checks()
    contract.sell_tax_pct = 25.0
    assert SecurityEvaluator(config).evaluate(_token("TAX"), holders, lp, contract).blocked


def test_l2_blocks_low_liquidity_to_mcap(config):
    token = _token("PAPER", liquidity_usd=100_000.0, market_cap_usd=50_000_000.0)
    holders, lp, contract = _clean_checks()
    report = SecurityEvaluator(config).evaluate(token, holders, lp, contract)
    assert report.blocked


def test_l2_missing_data_is_degraded_not_clean(config):
    """Нет данных ≠ всё хорошо: оценка снижается и появляется пометка."""
    report = SecurityEvaluator(config).evaluate(_token("UNKNOWN"), None, None, None)
    assert not report.blocked
    assert report.score < 100
    assert len(report.degraded) >= 3


def test_l2_filters_can_be_disabled(config):
    config.L2_CHECK_HOLDERS = False
    config.L2_CHECK_LP_LOCK = False
    config.L2_CHECK_CONTRACT = False
    holders, lp, contract = _clean_checks()
    holders.top10_pct = 99.0
    report = SecurityEvaluator(config).evaluate(_token("OFF"), holders, lp, contract)
    assert not report.blocked
    assert report.degraded


async def test_l2_blocks_known_scams_in_demo(config, provider):
    tokens = await provider.discover_candidates(50)
    by_symbol = {t.symbol: t for t in tokens}
    scanner = DeepScanner(config, provider, ai=None)
    survivors, stage = await scanner.run([by_symbol[s] for s in ("AURORA", "MOONX", "SAFER", "WHALE", "HONNY")])
    passed = {t.symbol for t, _ in survivors}
    assert "AURORA" in passed
    assert {"MOONX", "SAFER", "WHALE", "HONNY"}.isdisjoint(passed)
    assert stage.rejected == 4


# ═══════════════════════════════════════════════════════════════
#  УРОВЕНЬ 3 — ончейн
# ═══════════════════════════════════════════════════════════════
def test_l3_blocks_fresh_deployer(config):
    holders, lp, contract = _clean_checks()
    security = SecurityEvaluator(config).evaluate(_token("NEWDEV"), holders, lp, contract)
    deployer = _deployer(age_days=1.0)
    report = apply_deployer(security, deployer, config)
    assert report.blocked
    assert any("деплоер" in b.lower() for b in report.blockers)


def test_l3_blocks_serial_deployer(config):
    security = SecurityEvaluator(config).evaluate(_token("SERIAL"), *_clean_checks())
    report = apply_deployer(security, _deployer(tokens_deployed=40), config)
    assert report.blocked


def test_l3_blocks_deployer_who_sold_out(config):
    security = SecurityEvaluator(config).evaluate(_token("SOLD"), *_clean_checks())
    report = apply_deployer(security, _deployer(sold_out=True), config)
    assert report.blocked


def test_l3_passes_healthy_deployer(config):
    security = SecurityEvaluator(config).evaluate(_token("GOODDEV"), *_clean_checks())
    report = apply_deployer(security, _deployer(age_days=400, tokens_deployed=2, tx_count=1200), config)
    assert not report.blocked
    assert report.score > 80


def test_l3_marks_degraded_without_data(config):
    security = SecurityEvaluator(config).evaluate(_token("NODATA"), *_clean_checks())
    report = apply_deployer(security, None, config)
    assert not report.blocked
    assert report.degraded
    assert report.score < 100


def _deployer(**overrides):
    from v2.models import DeployerInfo

    base = dict(address="0xdev", age_days=200.0, tokens_deployed=3, tx_count=800,
                funded_by_age_hours=5000.0, sold_out=False, flagged=False)
    base.update(overrides)
    return DeployerInfo(**base)


async def test_l3_blocks_ember_in_demo(config, provider):
    """EMBER проходит L2, но деплоеру 2 дня — отсев на уровне 3."""
    tokens = {t.symbol: t for t in await provider.discover_candidates(50)}
    pairs, _ = await DeepScanner(config, provider, ai=None).run([tokens["EMBER"]])
    assert pairs, "EMBER должен пройти уровень 2"
    survivors, stage = await OnchainScanner(config, provider).run(pairs)
    assert survivors == []
    assert stage.rejected == 1


# ═══════════════════════════════════════════════════════════════
#  КОНВЕЙЕР
# ═══════════════════════════════════════════════════════════════
async def test_pipeline_funnel_is_monotonic(config, provider):
    result = await ScannerPipeline(config, provider).run(limit=50)
    counts = [stage.passed for stage in result.stages]
    assert counts[0] >= counts[1] >= counts[2]
    assert result.stages[0].entered == 14
    assert result.survivors
    assert all(not security.blocked for _, security in result.survivors)


async def test_pipeline_survives_provider_failure(config, provider, monkeypatch):
    """Поломка провайдера не роняет скан — уровень уходит в degraded."""
    async def boom(*_args, **_kwargs):
        raise RuntimeError("провайдер недоступен")

    monkeypatch.setattr(provider, "holders", boom)
    monkeypatch.setattr(provider, "lp_lock", boom)
    result = await ScannerPipeline(config, provider).run(limit=50)
    assert result.stages[0].passed > 0          # L1 отработал
    assert result.stages[1].degraded            # L2 честно сообщил о проблеме
    assert result.stages[1].passed > 0          # и не отбросил всё подряд
