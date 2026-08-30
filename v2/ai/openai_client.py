"""
AI-сервис: OpenAI-совместимый API + локальная заглушка.

Два места, где модель реально полезна:
  1. анализ контракта — по списку функций/флагов и метаданным верификации
     модель объясняет, чем именно опасен контракт (proxy-апгрейд, скрытый
     owner, возможность менять баланс);
  2. социальный скрининг — по набору твитов модель отделяет органический
     интерес от накрученного шиллинга.

Оба вызова опциональны: без OPENAI_API_KEY (или при AI_CONTRACT_ANALYSIS=false)
работает rule-based заглушка. Она не «молчит», а выдаёт честный разбор по
формальным признакам и помечает результат ``[без AI]``. Так бот остаётся
работоспособным офлайн и не врёт, что вывод сделан моделью.
"""

from __future__ import annotations

import json
from typing import Any

from v2.config import V2Config
from v2.core.errors import ProviderUnavailable
from v2.core.logging import get_logger
from v2.core.monitor import monitor
from v2.models import ContractRisk, SocialReport, TokenCandidate

logger = get_logger("ai.openai")

CONTRACT_SYSTEM = (
    "Ты — аудитор смарт-контрактов ERC-20/BEP-20. По списку флагов и функций оцени риск rug-pull, "
    "honeypot и скрытого контроля владельца. Ответь строго JSON: "
    '{"verdict": "safe|suspicious|dangerous", "notes": "2-4 предложения на русском", "key_risks": ["..."]}'
)
SOCIAL_SYSTEM = (
    "Ты — аналитик криптосообществ. По списку твитов определи, органический это интерес или накрутка/шиллинг. "
    'Ответь строго JSON: {"verdict": "organic|hype|risk", "notes": "2-3 предложения на русском"}'
)


class AIService:
    """Обёртка над chat/completions с деградацией в rule-based заглушку."""

    def __init__(self, config: V2Config, http=None) -> None:
        self.config = config
        self.http = http
        self.calls = 0
        self.fallbacks = 0

    @property
    def enabled(self) -> bool:
        return self.config.ai_available

    # ── вызов модели ─────────────────────────────────────────────
    async def _chat(self, system: str, user: str, *, component: str) -> dict[str, Any] | None:
        if not self.enabled or self.http is None:
            return None
        self.calls += 1
        url = f"{self.config.OPENAI_BASE_URL.rstrip('/')}/chat/completions"
        body = {
            "model": self.config.OPENAI_MODEL,
            "max_tokens": self.config.AI_MAX_TOKENS,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        try:
            payload = await self.http.post_json(
                url,
                json_body=body,
                headers={"Authorization": f"Bearer {self.config.OPENAI_API_KEY}"},
                component=component,
            )
        except ProviderUnavailable as exc:
            monitor.record(component, exc)
            self.fallbacks += 1
            return None
        try:
            content = payload["choices"][0]["message"]["content"]
            return json.loads(content)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            monitor.record(component, f"не удалось разобрать ответ модели: {exc}")
            self.fallbacks += 1
            return None

    # ── 1. анализ контракта ──────────────────────────────────────
    async def analyze_contract(
        self,
        token: TokenCandidate,
        contract: ContractRisk,
        source_meta: dict[str, Any] | None = None,
    ) -> ContractRisk:
        """
        Добавляет в ContractRisk текстовый разбор и вердикт.

        Возвращает тот же объект (мутирует ai_notes/ai_verdict), чтобы вызывающий
        код не пересобирал отчёт.
        """
        if not (self.enabled and self.config.AI_CONTRACT_ANALYSIS):
            contract.ai_notes, contract.ai_verdict = self._contract_stub(token, contract, source_meta)
            return contract

        prompt = json.dumps(
            {
                "token": token.symbol,
                "chain": token.chain,
                "flags": {
                    "mintable": contract.is_mintable,
                    "blacklist": contract.has_blacklist,
                    "honeypot": contract.is_honeypot,
                    "proxy": contract.is_proxy,
                    "owner_can_change_balance": contract.owner_can_change_balance,
                    "buy_tax_pct": contract.buy_tax_pct,
                    "sell_tax_pct": contract.sell_tax_pct,
                    "verified": contract.source_verified,
                },
                "functions": contract.functions_found,
                "source": source_meta or {},
            },
            ensure_ascii=False,
        )
        result = await self._chat(CONTRACT_SYSTEM, prompt, component="ai.contract")
        if result:
            contract.ai_notes = str(result.get("notes") or "")
            contract.ai_verdict = str(result.get("verdict") or "")
            risks = result.get("key_risks") or []
            if isinstance(risks, list) and risks:
                contract.ai_notes = (contract.ai_notes + " Ключевые риски: " + "; ".join(map(str, risks[:3]))).strip()
            return contract

        contract.ai_notes, contract.ai_verdict = self._contract_stub(token, contract, source_meta)
        return contract

    @staticmethod
    def _contract_stub(
        token: TokenCandidate, contract: ContractRisk, source_meta: dict[str, Any] | None
    ) -> tuple[str, str]:
        """Rule-based разбор: объясняет каждый флаг человеческим языком."""
        notes: list[str] = []
        verdict = "safe"

        if contract.is_mintable:
            notes.append("контракт может допечатывать токены — владелец способен обесценить вашу позицию")
            verdict = "dangerous"
        if contract.has_blacklist:
            notes.append("есть функция чёрного списка — адрес могут заблокировать и вы не продадите токен")
            verdict = "dangerous"
        if contract.is_honeypot:
            notes.append("признак honeypot: покупка проходит, продажа — нет")
            verdict = "dangerous"
        if contract.owner_can_change_balance:
            notes.append("владелец может менять балансы держателей")
            verdict = "dangerous"
        if contract.is_proxy:
            notes.append("контракт-прокси: логику можно заменить апгрейдом уже после вашей покупки")
            verdict = "suspicious" if verdict == "safe" else verdict
        if contract.cannot_sell_all:
            notes.append("проверка продажи отвечает отказом — возможен запрет на полный выход")
            verdict = "dangerous"
        if (contract.sell_tax_pct or 0) > 10:
            notes.append(f"налог на продажу {contract.sell_tax_pct:.1f}% — часть прибыли сгорает на выходе")
            verdict = "suspicious" if verdict == "safe" else verdict
        if contract.source_verified is False:
            notes.append("исходный код не верифицирован — поведение контракта проверить нельзя")
            verdict = "suspicious" if verdict == "safe" else verdict
        if source_meta and source_meta.get("proxy"):
            notes.append(f"реализация прокси: {source_meta.get('implementation') or 'не указана'}")

        if not notes:
            notes.append("опасных функций не найдено: нет mint, blacklist, honeypot; налоги в норме")
        if contract.source_verified and verdict == "safe":
            notes.append("исходный код верифицирован в эксплорере")
        return "[без AI] " + "; ".join(notes) + ".", verdict

    # ── 2. социальный скрининг ───────────────────────────────────
    async def screen_social(self, token: TokenCandidate, social: SocialReport) -> SocialReport:
        if not (self.enabled and self.config.AI_SOCIAL_SCREENING) or social.is_stub:
            if social.is_stub:
                social.ai_notes = "[без AI] хайп оценён по рыночным прокси, а не по соцсетям"
            return social

        prompt = json.dumps(
            {
                "token": token.symbol,
                "mentions": social.mentions,
                "unique_authors": social.unique_authors,
                "hype_score": social.hype_score,
                "posts": social.top_posts[:10],
            },
            ensure_ascii=False,
        )
        result = await self._chat(SOCIAL_SYSTEM, prompt, component="ai.social")
        if result:
            social.ai_notes = str(result.get("notes") or "")
            verdict = str(result.get("verdict") or "")
            if verdict == "risk":
                social.hype_score = max(0.0, social.hype_score - 25)
                social.ai_notes += " (оценка хайпа понижена: признаки накрутки)"
            social.keywords = list({*social.keywords, f"ai:{verdict}"}) if verdict else social.keywords
        else:
            social.ai_notes = "[без AI] модель недоступна — хайп по метрикам вовлечённости"
        return social

    def stats(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "calls": self.calls, "fallbacks": self.fallbacks}
