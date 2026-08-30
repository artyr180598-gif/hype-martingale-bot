"""
УРОВЕНЬ 2 — глубокий скам-фильтр.

Проверяет три независимых источника риска (все включаются/выключаются в .env):

  1. ХОЛДЕРЫ. Концентрация топ-10 > L2_MAX_TOP10_PCT (40%) означает, что
     десять кошельков могут обрушить цену одним выходом. Отдельно смотрим
     топ-1 (>25% — один кит держит рынок) и число держателей.

  2. ЛИКВИДНОСТЬ. LP должна быть заблокирована: и по доле
     (L2_MIN_LP_LOCKED_PCT), и по сроку (L2_MIN_LP_LOCK_DAYS = 180 дней).
     Разблокировка через месяц — это не защита, а отложенный rug.

  3. КОНТРАКТ. mint() — возможность допечатать токены; blacklist() —
     возможность заблокировать ваш адрес; honeypot — запрет продажи.
     Плюс налоги, прокси и верификация исходников. Флаги берём у GoPlus,
     а смысл объясняет AI-модуль (или rule-based заглушка).

Каждая непройденная проверка даёт либо блокер (токен не проходит уровень),
либо предупреждение (штраф к оценке безопасности). Проверки, которые не
удалось выполнить (провайдер лёг), попадают в ``degraded`` — мы не делаем вид,
 что всё чисто.
"""

from __future__ import annotations

import asyncio
import time

from v2.ai.openai_client import AIService
from v2.config import V2Config
from v2.core.logging import get_logger
from v2.core.monitor import health, monitor
from v2.data.provider import MarketProvider
from v2.models import (
    ContractRisk,
    HolderStats,
    LpLockInfo,
    ScanStageResult,
    SecurityReport,
    TokenCandidate,
)

logger = get_logger("scanner.l2")


class SecurityEvaluator:
    """Превращает сырые проверки в SecurityReport (оценка + блокеры)."""

    def __init__(self, config: V2Config) -> None:
        self.config = config

    def evaluate(
        self,
        token: TokenCandidate,
        holders: HolderStats | None,
        lp: LpLockInfo | None,
        contract: ContractRisk | None,
    ) -> SecurityReport:
        cfg = self.config
        report = SecurityReport(holders=holders or HolderStats(), lp=lp or LpLockInfo(),
                               contract=contract or ContractRisk())
        score = 100.0

        # ── 1. холдеры ───────────────────────────────────────────
        if not cfg.L2_CHECK_HOLDERS:
            report.degraded.append("проверка холдеров выключена")
        elif holders is None or holders.top10_pct is None:
            report.degraded.append("нет данных о холдерах")
            score -= 12
        else:
            if holders.top10_pct > cfg.L2_MAX_TOP10_PCT:
                report.blockers.append(
                    f"⛔ Концентрация топ-10 холдеров {holders.top10_pct:.1f}% > {cfg.L2_MAX_TOP10_PCT:.0f}% — "
                    "десять кошельков контролируют рынок"
                )
                score -= 35
            elif holders.top10_pct > cfg.L2_MAX_TOP10_PCT * 0.8:
                report.warnings.append(
                    f"Концентрация топ-10 {holders.top10_pct:.1f}% близка к порогу {cfg.L2_MAX_TOP10_PCT:.0f}%"
                )
                score -= 10
            else:
                report.passed.append(
                    f"✅ Топ-10 холдеров держат {holders.top10_pct:.1f}% (порог {cfg.L2_MAX_TOP10_PCT:.0f}%)"
                )

            if holders.top1_pct is not None and holders.top1_pct > cfg.L2_MAX_TOP1_PCT:
                report.blockers.append(
                    f"⛔ Один кошелёк держит {holders.top1_pct:.1f}% > {cfg.L2_MAX_TOP1_PCT:.0f}% — "
                    "риск мгновенного дампа"
                )
                score -= 25
            if holders.holders_count is not None and holders.holders_count < cfg.L2_MIN_HOLDERS:
                report.warnings.append(
                    f"Держателей всего {holders.holders_count} (< {cfg.L2_MIN_HOLDERS}) — узкая база"
                )
                score -= 8
            elif holders.holders_count:
                report.passed.append(f"✅ Держателей: {holders.holders_count}")

        # ── 2. ликвидность ───────────────────────────────────────
        if not cfg.L2_CHECK_LP_LOCK:
            report.degraded.append("проверка блокировки LP выключена")
        elif lp is None or lp.locked_pct is None:
            report.degraded.append("нет данных о блокировке LP")
            score -= 15
        else:
            if lp.locked_pct < cfg.L2_MIN_LP_LOCKED_PCT:
                report.blockers.append(
                    f"⛔ Заблокировано только {lp.locked_pct:.0f}% LP (< {cfg.L2_MIN_LP_LOCKED_PCT:.0f}%) — "
                    "ликвидность можно вывести"
                )
                score -= 40
            elif lp.lock_days_left is not None and lp.lock_days_left < cfg.L2_MIN_LP_LOCK_DAYS:
                report.blockers.append(
                    f"⛔ LP заблокирована на {lp.lock_days_left:.0f} дней (< {cfg.L2_MIN_LP_LOCK_DAYS}) — "
                    "слишком короткий лок"
                )
                score -= 30
            else:
                lock_text = "навсегда" if lp.locked_forever else f"{lp.lock_days_left:.0f} дней"
                report.passed.append(
                    f"✅ LP заблокирована на {lp.locked_pct:.0f}% ({lock_text})"
                    + (f", локировщик: {lp.locker}" if lp.locker else "")
                )

        ratio = token.liq_to_mcap
        if ratio is None:
            report.warnings.append("Капитализация неизвестна — соотношение LP/капа не проверить")
        elif ratio < cfg.L2_MIN_LIQ_TO_MCAP:
            report.blockers.append(
                f"⛔ Ликвидность ${token.liquidity_usd:,.0f} = {ratio * 100:.2f}% от капы "
                f"(< {cfg.L2_MIN_LIQ_TO_MCAP * 100:.1f}%) — выход из позиции двигает цену"
            )
            score -= 25
        elif ratio > cfg.L2_MAX_LIQ_TO_MCAP:
            report.warnings.append(
                f"LP составляет {ratio * 100:.0f}% капитализации — рынок почти пуст"
            )
            score -= 8
        else:
            report.passed.append(f"✅ LP/капа = {ratio * 100:.1f}% — ликвидность соразмерна оценке")

        # ── 3. контракт ──────────────────────────────────────────
        if not cfg.L2_CHECK_CONTRACT:
            report.degraded.append("проверка контракта выключена")
        elif contract is None:
            report.degraded.append("нет данных о контракте")
            score -= 15
        else:
            if contract.is_mintable and cfg.L2_BLOCK_IF_MINTABLE:
                report.blockers.append("⛔ В контракте есть mint() — владелец может допечатать токены")
                score -= 45
            if contract.has_blacklist and cfg.L2_BLOCK_IF_BLACKLIST:
                report.blockers.append("⛔ Есть blacklist() — ваш адрес могут заблокировать")
                score -= 35
            if contract.is_honeypot and cfg.L2_BLOCK_IF_HONEYPOT:
                report.blockers.append("⛔ Признак honeypot: продать токен, скорее всего, не получится")
                score -= 50
            if contract.cannot_sell_all:
                report.blockers.append("⛔ Симуляция продажи не проходит — выход из позиции заблокирован")
                score -= 40
            if (contract.sell_tax_pct or 0) > cfg.L2_MAX_SELL_TAX_PCT:
                report.blockers.append(
                    f"⛔ Налог на продажу {contract.sell_tax_pct:.1f}% > {cfg.L2_MAX_SELL_TAX_PCT:.0f}%"
                )
                score -= 25
            elif (contract.sell_tax_pct or 0) > cfg.L2_MAX_SELL_TAX_PCT / 2:
                report.warnings.append(f"Налог на продажу {contract.sell_tax_pct:.1f}% — заметно режет прибыль")
                score -= 6
            if (contract.buy_tax_pct or 0) > cfg.L2_MAX_BUY_TAX_PCT:
                report.warnings.append(f"Налог на покупку {contract.buy_tax_pct:.1f}%")
                score -= 5
            if contract.is_proxy:
                report.warnings.append("Контракт-прокси: логику можно изменить апгрейдом")
                score -= 8
            if contract.source_verified is False and cfg.L2_REQUIRE_VERIFIED_SOURCE:
                report.blockers.append("⛔ Исходный код не верифицирован (включён строгий режим)")
                score -= 20
            elif contract.source_verified is False:
                report.warnings.append("Исходный код не верифицирован — поведение непрозрачно")
                score -= 12
            if contract.source_verified and not (contract.is_mintable or contract.has_blacklist):
                report.passed.append("✅ Опасных функций (mint/blacklist/honeypot) не найдено")

        report.score = max(0.0, min(100.0, score))
        report.blocked = bool(report.blockers)
        return report


class DeepScanner:
    name = "L2 скам-фильтр"

    def __init__(self, config: V2Config, provider: MarketProvider, ai: AIService | None = None) -> None:
        self.config = config
        self.provider = provider
        self.ai = ai
        self.evaluator = SecurityEvaluator(config)

    async def run(
        self, candidates: list[TokenCandidate]
    ) -> tuple[list[tuple[TokenCandidate, SecurityReport]], ScanStageResult]:
        stage = ScanStageResult(level=2, name=self.name)
        started = time.time()
        stage.entered = len(candidates)
        if not candidates:
            stage.duration_sec = time.time() - started
            return [], stage

        if not self.config.SCAN_L2_ENABLED:
            stage.degraded.append("фильтр уровня 2 выключен конфигурацией — скам-проверки пропущены")
            out = [(t, SecurityReport(score=50.0, degraded=["L2 выключен"])) for t in candidates]
            stage.passed = len(out)
            stage.duration_sec = time.time() - started
            return out, stage

        semaphore = asyncio.Semaphore(max(1, self.config.L2_CONCURRENCY))

        async def check(token: TokenCandidate) -> tuple[TokenCandidate, SecurityReport] | None:
            async with semaphore:
                return await self._check_one(token, stage)

        results = await asyncio.gather(*(check(t) for t in candidates), return_exceptions=True)
        out: list[tuple[TokenCandidate, SecurityReport]] = []
        for item in results:
            if isinstance(item, BaseException):
                monitor.record("scanner.l2", item)
                stage.note("ошибка проверки")
                continue
            if item is None:
                continue
            token, security = item
            if security.blocked:
                stage.note(security.blockers[0].split("—")[0].strip("⛔ ").strip()[:60])
                continue
            out.append((token, security))

        out.sort(key=lambda pair: pair[1].score, reverse=True)
        stage.passed = len(out)
        stage.rejected = stage.entered - stage.passed
        stage.duration_sec = time.time() - started
        health.mark_ok("scanner.l2", passed=stage.passed)
        logger.info(
            "L2: вошло %d → прошло %d (заблокировано %d) за %.1fс",
            stage.entered, stage.passed, stage.rejected, stage.duration_sec,
        )
        return out, stage

    async def _check_one(
        self, token: TokenCandidate, stage: ScanStageResult
    ) -> tuple[TokenCandidate, SecurityReport]:
        """Три проверки параллельно: ни одна не блокирует остальные."""
        holders_task = self.provider.holders(token)
        lp_task = self.provider.lp_lock(token)
        contract_task = self.provider.contract_risk(token)
        holders, lp, contract = await asyncio.gather(
            _safe(holders_task, "holders", stage),
            _safe(lp_task, "lp", stage),
            _safe(contract_task, "contract", stage),
        )

        report = self.evaluator.evaluate(token, holders, lp, contract)

        # AI-разбор контракта (или заглушка) — только если токен не заблокирован
        if self.ai is not None and report.contract.source != "":
            try:
                report.contract = await self.ai.analyze_contract(token, report.contract)
            except Exception as exc:  # noqa: BLE001
                monitor.record("scanner.l2.ai", exc)
        if report.contract.ai_verdict == "dangerous":
            report.blockers.append("⛔ AI-аудит контракта: опасные механизмы контроля")
            report.blocked = True
        elif report.contract.ai_verdict == "suspicious":
            report.warnings.append(f"AI-аудит: {report.contract.ai_notes}")
            report.score = max(0.0, report.score - 10)
        return token, report


async def _safe(awaitable, label: str, stage: ScanStageResult):
    """Обёртка: ошибка провайдера не должна ронять весь уровень."""
    try:
        return await awaitable
    except Exception as exc:  # noqa: BLE001
        monitor.record(f"scanner.l2.{label}", exc)
        stage.degraded.append(f"{label}: {type(exc).__name__}")
        return None
