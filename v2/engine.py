"""
Движок анализа по запросу — «мозг» бота.

Отвечает на запрос вида «проанализируй 0xABC…» или «проанализируй AURORA»
и собирает CoinReport из пяти блоков (по ТЗ):

  1. Безопасность   — пул, LP-лок, опасные функции, профиль деплоера;
  2. Микроструктура — стакан: стены, глубина, проскальзывание входа на $5k;
  3. Теханализ      — тренд H1 (ADX), накопление M15 (OBV), уровни Фибоначчи;
  4. Соцфон         — упоминания за 2 часа (X API или эмуляция);
  5. Вердикт        — риск 1–10, размер позиции, стоп/цель с R:R ≥ 1:2.

Все сетевые запросы идут параллельно (asyncio.gather) и изолированы: отказ
одного провайдера не отменяет отчёт, а помечает раздел как degraded.
"""

from __future__ import annotations

import asyncio
import time

from v2.ai.openai_client import AIService
from v2.analysis.microstructure import analyze_orderbook
from v2.analysis.risk_manager import assemble_coin_report
from v2.analysis.technical import build_technical_report
from v2.config import V2Config
from v2.core.errors import TokenNotFound
from v2.core.logging import get_logger
from v2.core.monitor import health, monitor
from v2.data.provider import MarketProvider
from v2.models import Candle, CoinReport, MicrostructureReport, SocialReport, TokenCandidate
from v2.scanner.level2_deep import SecurityEvaluator
from v2.scanner.level3_onchain import apply_deployer

logger = get_logger("engine")


class AnalysisEngine:
    def __init__(self, config: V2Config, provider: MarketProvider, ai: AIService | None = None) -> None:
        self.config = config
        self.provider = provider
        self.ai = ai or AIService(config)
        self.evaluator = SecurityEvaluator(config)
        self.analyses = 0

    # ═══════════════════════════════════════════════════════════
    async def analyze(
        self,
        query: str,
        *,
        token: TokenCandidate | None = None,
        deposit_usd: float | None = None,
    ) -> CoinReport:
        started = time.time()
        deposit = deposit_usd if deposit_usd is not None else self.config.DEFAULT_DEPOSIT_USD
        token = token or await self._resolve(query)
        logger.info("Анализ %s (%s) — запрос «%s»", token.symbol, token.chain, query)

        candles_trend, candles_accum, book, holders, lp, contract, deployer, social = await asyncio.gather(
            self._klines(token, self.config.ANALYSIS_TREND_TF),
            self._klines(token, self.config.ANALYSIS_ACCUM_TF),
            self._orderbook(token),
            self._holders(token),
            self._lp(token),
            self._contract(token),
            self._deployer(token),
            self._social(token),
        )

        # ── 1. безопасность ──────────────────────────────────────
        security = self.evaluator.evaluate(token, holders, lp, contract)
        if contract is not None:
            try:
                security.contract = await self.ai.analyze_contract(token, security.contract)
            except Exception as exc:  # noqa: BLE001
                monitor.record("engine.ai.contract", exc)
        security = apply_deployer(security, deployer, self.config)
        if security.contract.ai_verdict == "dangerous" and not security.blocked:
            security.blockers.append("⛔ AI-аудит контракта: опасные механизмы контроля")
            security.blocked = True

        # ── 2. микроструктура ────────────────────────────────────
        micro = analyze_orderbook(book, self.config.ANALYSIS_ENTRY_SIZE_USD) if book else _empty_micro(
            self.config.ANALYSIS_ENTRY_SIZE_USD
        )

        # ── 3. теханализ ─────────────────────────────────────────
        candles_by_tf = {
            self.config.ANALYSIS_TREND_TF: candles_trend,
            self.config.ANALYSIS_ACCUM_TF: candles_accum,
        }
        direction_hint = 1
        technical = build_technical_report(candles_by_tf, self.config, direction_hint=direction_hint)
        if not technical.price and token.price_usd:
            technical.price = token.price_usd
        if not token.price_usd and technical.price:
            token.price_usd = technical.price

        # ── 4. соцфон ────────────────────────────────────────────
        if social is not None:
            try:
                social = await self.ai.screen_social(token, social)
            except Exception as exc:  # noqa: BLE001
                monitor.record("engine.ai.social", exc)

        # ── 5. вердикт ───────────────────────────────────────────
        report = assemble_coin_report(
            token,
            security,
            technical,
            micro,
            social or _empty_social(self.config.ANALYSIS_SOCIAL_WINDOW_HOURS),
            self.config,
            deposit_usd=deposit,
        )
        report.duration_sec = time.time() - started
        self.analyses += 1
        health.mark_ok("engine.analyze", symbol=token.symbol, verdict=report.verdict)
        logger.info(
            "%s: вердикт %s, риск %d/10, R:R 1:%.1f (%.1fс)",
            token.symbol, report.verdict, report.risk_score, report.plan.rr, report.duration_sec,
        )
        return report

    # ═══════════════════════════════════════════════════════════
    async def _resolve(self, query: str) -> TokenCandidate:
        query = query.strip()
        if not query:
            raise TokenNotFound("пустой запрос")
        found = await self.provider.resolve_token(query)
        if not found:
            raise TokenNotFound(f"«{query}» не найден ни у одного провайдера")
        # основной пул = с наибольшей ликвидностью
        best = max(found, key=lambda t: t.liquidity_usd)
        if len(found) > 1:
            logger.info("Найдено пулов: %d — беру %s (ликвидность $%.0f)", len(found), best.dex, best.liquidity_usd)
        return best

    async def _klines(self, token: TokenCandidate, timeframe: str) -> list[Candle]:
        try:
            return await self.provider.klines(token, timeframe, self.config.ANALYSIS_BARS)
        except Exception as exc:  # noqa: BLE001
            monitor.record(f"engine.klines.{timeframe}", exc)
            return []

    async def _orderbook(self, token: TokenCandidate):
        try:
            return await self.provider.orderbook(token, self.config.ANALYSIS_ORDERBOOK_DEPTH)
        except Exception as exc:  # noqa: BLE001
            monitor.record("engine.orderbook", exc)
            return None

    async def _holders(self, token: TokenCandidate):
        try:
            return await self.provider.holders(token)
        except Exception as exc:  # noqa: BLE001
            monitor.record("engine.holders", exc)
            return None

    async def _lp(self, token: TokenCandidate):
        try:
            return await self.provider.lp_lock(token)
        except Exception as exc:  # noqa: BLE001
            monitor.record("engine.lp", exc)
            return None

    async def _contract(self, token: TokenCandidate):
        try:
            return await self.provider.contract_risk(token)
        except Exception as exc:  # noqa: BLE001
            monitor.record("engine.contract", exc)
            return None

    async def _deployer(self, token: TokenCandidate):
        try:
            return await self.provider.deployer(token)
        except Exception as exc:  # noqa: BLE001
            monitor.record("engine.deployer", exc)
            return None

    async def _social(self, token: TokenCandidate):
        try:
            return await self.provider.social(token, self.config.ANALYSIS_SOCIAL_WINDOW_HOURS)
        except Exception as exc:  # noqa: BLE001
            monitor.record("engine.social", exc)
            return None


def _empty_micro(entry_size: float) -> MicrostructureReport:
    report = MicrostructureReport(entry_size_usd=entry_size)
    report.grade = "empty"
    report.notes.append("Стакан недоступен — проскальзывание не оценить, вход только лимитным ордером")
    return report


def _empty_social(window_hours: int) -> SocialReport:
    report = SocialReport(window_hours=window_hours, source="unavailable", is_stub=True)
    report.top_posts = ["[нет данных] социальный фон не получен"]
    return report
