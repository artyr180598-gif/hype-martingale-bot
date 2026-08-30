"""
УРОВЕНЬ 3 — ончейн-сканер (профиль деплоера).

Идея: скам-контракт можно написать идеально, но поведение создавшего его
кошелька скрыть нельзя. Смотрим:

  * возраст кошелька — L3_MIN_DEPLOYER_AGE_DAYS (7 дней). Одноразовый адрес,
    созданный за час до деплоя, почти всегда означает серийный запуск;
  * сколько контрактов он задеплоил — L3_MAX_DEPLOYER_TOKENS (25). Конвейер
    токенов = конвейер рагов;
  * общая активность (L3_MIN_DEPLOYER_TX_COUNT) — «пустой» кошелёк без истории
    подозрителен не меньше свежего;
  * возраст источника финансирования — если кошелёк пополнили за час до деплоя,
    деплоер не самостоятелен (обычно это кошелёк-прокладка);
  * продал ли деплоер весь свой стейк (sold_out) и не помечен ли он в
    чёрных списках (flagged).

Данные берём у эксплорера (Etherscan v2 / BscScan) или у Moralis. Если ключей
нет — уровень честно помечается degraded и токены проходят с пометкой
«ончейн-проверка не выполнена» (в отчёте это видно).
"""

from __future__ import annotations

import asyncio
import time

from v2.config import V2Config
from v2.core.logging import get_logger
from v2.core.monitor import health, monitor
from v2.data.provider import MarketProvider
from v2.models import DeployerInfo, ScanStageResult, SecurityReport, TokenCandidate

logger = get_logger("scanner.l3")


def apply_deployer(
    report: SecurityReport, deployer: DeployerInfo | None, config: V2Config
) -> SecurityReport:
    """Дополняет SecurityReport выводами по деплоеру (мутирует и возвращает)."""
    report.deployer = deployer or DeployerInfo()
    if not config.SCAN_L3_ENABLED:
        report.degraded.append("ончейн-проверка выключена")
        return report
    if deployer is None or deployer.age_days is None:
        report.degraded.append("нет данных о деплоере (нужен ETHERSCAN_API_KEY/BSCSCAN_API_KEY)")
        report.score = max(0.0, report.score - 10)
        return report

    d = deployer
    if d.age_days is not None and d.age_days < config.L3_MIN_DEPLOYER_AGE_DAYS:
        report.blockers.append(
            f"⛔ Кошелёк деплоера существует {d.age_days:.1f} дней (< {config.L3_MIN_DEPLOYER_AGE_DAYS}) — "
            "одноразовый адрес под запуск"
        )
        report.score -= 30
    elif d.age_days is not None:
        report.passed.append(f"✅ Возраст кошелька деплоера: {d.age_days:.0f} дней")

    if d.tokens_deployed is not None and d.tokens_deployed > config.L3_MAX_DEPLOYER_TOKENS:
        report.blockers.append(
            f"⛔ Деплоер создал {d.tokens_deployed} контрактов (> {config.L3_MAX_DEPLOYER_TOKENS}) — "
            "серийный запуск токенов"
        )
        report.score -= 35
    elif d.tokens_deployed:
        report.passed.append(f"✅ Контрактов у деплоера: {d.tokens_deployed}")

    if d.tx_count is not None and d.tx_count < config.L3_MIN_DEPLOYER_TX_COUNT:
        report.warnings.append(
            f"У деплоера всего {d.tx_count} транзакций (< {config.L3_MIN_DEPLOYER_TX_COUNT}) — пустая история"
        )
        report.score -= 12
    elif d.tx_count:
        report.passed.append(f"✅ История деплоера: {d.tx_count} транзакций")

    if (
        d.funded_by_age_hours is not None
        and d.funded_by_age_hours < config.L3_MAX_DEPLOYER_FUNDED_AGE_HOURS
    ):
        report.warnings.append(
            f"Кошелёк профинансирован {d.funded_by_age_hours:.0f}ч назад — деплоер может быть прокладкой"
        )
        report.score -= 8

    if d.sold_out and config.L3_BLOCK_IF_DEPLOYER_SOLD:
        report.blockers.append("⛔ Деплоер продал весь свой стейк — классический паттерн rug-pull")
        report.score -= 40
    if d.flagged:
        report.blockers.append("⛔ Адрес деплоера помечен в чёрных списках")
        report.score -= 40
    if d.prior_projects:
        report.warnings.append(f"Предыдущие проекты деплоера: {', '.join(d.prior_projects[:3])}")

    report.score = max(0.0, min(100.0, report.score))
    report.blocked = report.blocked or bool(report.blockers)
    return report


class OnchainScanner:
    name = "L3 ончейн-профиль"

    def __init__(self, config: V2Config, provider: MarketProvider) -> None:
        self.config = config
        self.provider = provider

    async def run(
        self, pairs: list[tuple[TokenCandidate, SecurityReport]]
    ) -> tuple[list[tuple[TokenCandidate, SecurityReport]], ScanStageResult]:
        stage = ScanStageResult(level=3, name=self.name)
        started = time.time()
        stage.entered = len(pairs)
        if not pairs:
            stage.duration_sec = time.time() - started
            return [], stage

        if not self.config.SCAN_L3_ENABLED:
            for _, security in pairs:
                security.degraded.append("ончейн-проверка выключена конфигурацией")
            stage.passed = len(pairs)
            stage.degraded.append("фильтр уровня 3 выключен конфигурацией")
            stage.duration_sec = time.time() - started
            return pairs, stage

        semaphore = asyncio.Semaphore(max(1, self.config.L3_CONCURRENCY))

        async def check(token: TokenCandidate, security: SecurityReport):
            async with semaphore:
                try:
                    deployer = await self.provider.deployer(token)
                except Exception as exc:  # noqa: BLE001
                    monitor.record("scanner.l3", exc)
                    stage.degraded.append(f"deployer: {type(exc).__name__}")
                    deployer = None
                return token, apply_deployer(security, deployer, self.config)

        results = await asyncio.gather(*(check(t, s) for t, s in pairs), return_exceptions=True)
        out: list[tuple[TokenCandidate, SecurityReport]] = []
        for item in results:
            if isinstance(item, BaseException):
                monitor.record("scanner.l3", item)
                stage.note("ошибка ончейн-проверки")
                continue
            token, security = item
            if security.blocked:
                stage.note(security.blockers[-1].split("—")[0].strip("⛔ ").strip()[:60])
                continue
            out.append((token, security))

        out.sort(key=lambda pair: pair[1].score, reverse=True)
        stage.passed = len(out)
        stage.rejected = stage.entered - stage.passed
        stage.duration_sec = time.time() - started
        health.mark_ok("scanner.l3", passed=stage.passed)
        logger.info(
            "L3: вошло %d → прошло %d (заблокировано %d) за %.1fс",
            stage.entered, stage.passed, stage.rejected, stage.duration_sec,
        )
        return out, stage
