"""
УРОВЕНЬ 1 — быстрый сканер.

Задача: за один проход отсечь мусор, оставив только живые пулы. Критерии (ТЗ):
  * оборот за 5 минут > L1_MIN_VOLUME_5M_USD (по умолчанию $500 000);
  * транзакций за 5 минут > L1_MIN_TX_5M (по умолчанию 100);
плюс гигиена: стейблкойны/обёртки, пулы без ликвидности, пары не к стейблу,
слишком свежие пулы (шум первых часов) и слишком широкий спред.

Почему именно 5-минутное окно: суточный оборот есть и у мёртвых токенов
(его «накручивают» wash-трейдингом), а устойчивый пятиминутный поток сделок
подделать дорого. Число транзакций ловит второй случай — крупный оборот
одной сделкой кита не проходит фильтр.

Каждый отказ логируется с причиной: в отчёте скана видно, сколько монет и
почему отсеяно.
"""

from __future__ import annotations

import time

from v2.config import V2Config
from v2.core.logging import get_logger
from v2.core.monitor import health, monitor
from v2.data.provider import MarketProvider
from v2.models import ScanStageResult, TokenCandidate

logger = get_logger("scanner.l1")


class QuickScanner:
    name = "L1 быстрый фильтр"

    def __init__(self, config: V2Config, provider: MarketProvider) -> None:
        self.config = config
        self.provider = provider

    async def run(self, limit: int = 150) -> tuple[list[TokenCandidate], ScanStageResult]:
        stage = ScanStageResult(level=1, name=self.name)
        started = time.time()
        try:
            pool = await self.provider.discover_candidates(limit)
        except Exception as exc:  # noqa: BLE001 — сканер не должен ронять процесс
            monitor.record("scanner.l1", exc, fatal=False)
            stage.degraded.append(f"не удалось получить список пулов: {exc}")
            pool = []

        stage.entered = len(pool)
        if not pool:
            stage.duration_sec = time.time() - started
            return [], stage

        survivors: list[TokenCandidate] = []
        for token in pool:
            reason = self.reject_reason(token)
            if reason:
                stage.note(reason)
                continue
            survivors.append(token)

        # если фильтр выключен — пропускаем всё, но сортируем по активности
        if not self.config.SCAN_L1_ENABLED:
            stage.degraded.append("фильтр уровня 1 выключен конфигурацией")
            survivors = list(pool)

        survivors.sort(key=heat_score, reverse=True)
        survivors = survivors[: self.config.L1_MAX_CANDIDATES]

        stage.passed = len(survivors)
        stage.rejected = stage.entered - stage.passed
        stage.duration_sec = time.time() - started
        health.mark_ok("scanner.l1", candidates=stage.passed, entered=stage.entered)
        logger.info(
            "L1: вошло %d → прошло %d (отсеяно %d) за %.1fс",
            stage.entered, stage.passed, stage.rejected, stage.duration_sec,
        )
        return survivors, stage

    # ── причина отказа (или None, если токен проходит) ───────────
    def reject_reason(self, token: TokenCandidate) -> str | None:
        cfg = self.config
        symbol = token.symbol.upper()

        if symbol in cfg.blocklist_symbols:
            return "стейблкойн/обёртка (не актив)"
        if token.price_usd <= 0:
            return "нет цены"
        if cfg.quote_whitelist and token.quote_symbol.upper() not in cfg.quote_whitelist:
            return f"пара к {token.quote_symbol or '?'} (нужен стейбл/ETH/SOL)"
        if token.volume_5m_usd < cfg.L1_MIN_VOLUME_5M_USD:
            return f"объём 5м ${token.volume_5m_usd:,.0f} < ${cfg.L1_MIN_VOLUME_5M_USD:,.0f}"
        if token.tx_5m < cfg.L1_MIN_TX_5M:
            return f"транзакций за 5м {token.tx_5m} < {cfg.L1_MIN_TX_5M}"
        if token.liquidity_usd < cfg.L1_MIN_LIQUIDITY_USD:
            return f"ликвидность ${token.liquidity_usd:,.0f} < ${cfg.L1_MIN_LIQUIDITY_USD:,.0f}"
        age = token.age_hours
        if age and age < cfg.L1_MIN_PAIR_AGE_HOURS:
            return f"пул моложе {cfg.L1_MIN_PAIR_AGE_HOURS:.0f}ч (возраст {age:.1f}ч)"
        if age and age > cfg.L1_MAX_PAIR_AGE_HOURS:
            return "пул старше заданного горизонта"
        liq_ratio = token.liq_to_mcap
        if liq_ratio is not None and liq_ratio < cfg.L2_MIN_LIQ_TO_MCAP:
            return f"ликвидность/капа {liq_ratio * 100:.2f}% < {cfg.L2_MIN_LIQ_TO_MCAP * 100:.1f}%"
        return None


def heat_score(token: TokenCandidate) -> float:
    """
    «Нагретость» токена для сортировки внутри уровня 1.

    Объём за 5 минут относительно суточной нормы (ускорение интереса),
    перевес покупок и свежесть импульса. Не является оценкой качества —
    только порядком просмотра на уровне 2.
    """
    expected_5m = token.volume_24h_usd / 288.0
    acceleration = token.volume_5m_usd / expected_5m if expected_5m > 0 else 1.0
    score = min(acceleration, 20.0) * 4.0
    score += (token.buy_ratio_5m - 0.5) * 20.0
    score += min(15.0, max(0.0, token.price_change_1h_pct))
    if token.tx_5m > 500:
        score += 6.0
    return round(score, 2)
