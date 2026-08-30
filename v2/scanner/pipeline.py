"""
Конвейер трёхуровневого сканера.

Порядок уровней жёсткий и дешёвый → дорогой:
  L1 (тикеры, без свечей) → L2 (3 запроса на токен: холдеры/LP/контракт)
  → L3 (профиль деплоера) → полный анализ выживших.

Так сканер на 150 пулах делает ~150 «бесплатных» проверок, ~40 тройных
запросов и 10–15 ончейн-запросов вместо 450 запросов «в лоб». Каждый уровень
возвращает ScanStageResult с причинами отсева — они попадают в отчёт, поэтому
пользователь видит не только «что нашлось», но и «что и почему отброшено».
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from v2.config import V2Config
from v2.core.logging import get_logger
from v2.core.monitor import health, monitor
from v2.data.provider import MarketProvider
from v2.models import CoinReport, ScanResult, TokenCandidate
from v2.scanner.level1_quick import QuickScanner
from v2.scanner.level2_deep import DeepScanner
from v2.scanner.level3_onchain import OnchainScanner

if TYPE_CHECKING:  # только для подсказок — импорт в рантайме ленивый
    from v2.ai.openai_client import AIService
    from v2.engine import AnalysisEngine

logger = get_logger("scanner.pipeline")


class ScannerPipeline:
    def __init__(
        self,
        config: V2Config,
        provider: MarketProvider,
        ai: "AIService | None" = None,
        engine: "AnalysisEngine | None" = None,
    ) -> None:
        self.config = config
        self.provider = provider
        self.l1 = QuickScanner(config, provider)
        self.l2 = DeepScanner(config, provider, ai)
        self.l3 = OnchainScanner(config, provider)
        self.engine = engine
        self.last_result: ScanResult | None = None

    async def run(self, limit: int = 150, analyze_top: int = 0) -> ScanResult:
        started = time.time()
        result = ScanResult(mode=str(self.config.DATA_MODE))

        # ── УРОВЕНЬ 1 ────────────────────────────────────────────
        candidates, stage1 = await self.l1.run(limit)
        result.stages.append(stage1)

        # ── УРОВЕНЬ 2 ────────────────────────────────────────────
        survivors, stage2 = await self.l2.run(candidates)
        result.stages.append(stage2)

        # ── УРОВЕНЬ 3 ────────────────────────────────────────────
        survivors, stage3 = await self.l3.run(survivors)
        result.stages.append(stage3)
        result.survivors = survivors[: self.config.SCAN_TOP_RESULTS]

        # ── полный анализ топов (по запросу) ─────────────────────
        if analyze_top and self.engine is not None and result.survivors:
            for token, _security in result.survivors[:analyze_top]:
                try:
                    report = await self.engine.analyze(token.address, token=token)
                except Exception as exc:  # noqa: BLE001 — одна монета не ломает скан
                    monitor.record("scanner.pipeline.analyze", exc)
                    result.errors.append(f"{token.symbol}: {exc}")
                    continue
                if report.score >= self.config.SCAN_MIN_FINAL_SCORE:
                    result.reports.append(report)
            result.reports.sort(key=lambda r: r.score, reverse=True)

        result.duration_sec = time.time() - started
        # ошибки, случившиеся во время этого скана (из общего монитора)
        result.errors.extend(
            f"{r.component}: {r.kind}: {r.message[:80]}"
            for r in monitor.recent(limit=50)
            if r.ts >= started
        )
        self.last_result = result
        health.mark_ok(
            "scanner.pipeline",
            entered=result.total_in,
            survived=len(result.survivors),
            analyzed=len(result.reports),
        )
        logger.info(
            "Скан завершён за %.1fс: %d → L1 %d → L2 %d → L3 %d",
            result.duration_sec,
            result.total_in,
            stage1.passed,
            stage2.passed,
            stage3.passed,
        )
        return result

    async def quick_lookup(self, token: TokenCandidate) -> list[str]:
        """Проверить один токен по всем фильтрам (для отладки и /check в боте)."""
        reason = self.l1.reject_reason(token)
        if reason:
            return [f"L1: {reason}"]
        pairs, _ = await self.l2.run([token])
        notes: list[str] = []
        for _, security in pairs:
            notes.extend(security.blockers or security.warnings or ["L2: замечаний нет"])
        return notes or ["L1/L2: замечаний нет"]
