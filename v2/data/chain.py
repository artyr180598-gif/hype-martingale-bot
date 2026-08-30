"""
Ончейн-провайдер (уровень 3): Etherscan v2 / BscScan / Moralis.

Что проверяем про деплоера:
  * возраст кошелька (первая транзакция) — свежий кошелёк под один запуск
    это классический признак одноразового скама;
  * сколько контрактов он задеплоил — серийные деплоеры штампуют токены;
  * число транзакций вообще — «пустой» кошелёк без истории подозрителен;
  * откуда он профинансирован и насколько свежий этот источник;
  * продал ли деплоер весь свой стейк (rug-паттерн).

Etherscan v2 — единая точка входа для всех EVM-сетей (параметр chainid),
поэтому один класс закрывает ETH/BSC/Base/Arbitrum. Moralis используется как
запасной источник холдеров, если GoPlus не ответил.

ВАЖНО: без ключа API провайдер не работает и возвращает None — уровень 3
помечается как degraded, сканер продолжает работу без ончейн-проверки.
"""

from __future__ import annotations

from typing import Any

from v2.config import V2Config
from v2.core.errors import ProviderUnavailable
from v2.core.logging import get_logger
from v2.core.monitor import monitor
from v2.data.provider import MarketProvider
from v2.models import DeployerInfo, HolderStats, TokenCandidate, now_ms

logger = get_logger("data.chain")

ETHERSCAN_V2 = "https://api.etherscan.io/v2/api"
MORALIS = "https://deep-index.moralis.io/api/v2.2"

# chain → chainid для Etherscan v2
CHAIN_ID: dict[str, int] = {
    "ethereum": 1,
    "bsc": 56,
    "base": 8453,
    "arbitrum": 42161,
    "polygon": 137,
    "avalanche": 43114,
    "optimism": 10,
}
MORALIS_CHAIN: dict[str, str] = {"ethereum": "eth", "bsc": "bsc", "base": "base", "polygon": "polygon"}


def _api_key(config: V2Config, chain: str) -> str:
    if chain == "bsc" and config.BSCSCAN_API_KEY:
        return config.BSCSCAN_API_KEY
    return config.ETHERSCAN_API_KEY


class ExplorerProvider(MarketProvider):
    name = "explorer"

    def __init__(self, config: V2Config, http) -> None:
        self.config = config
        self.http = http

    async def _etherscan(self, chain: str, params: dict[str, Any]) -> Any:
        chain_id = CHAIN_ID.get(chain.lower())
        key = _api_key(self.config, chain)
        if not chain_id or not key:
            return None
        payload = await self.http.get_json(
            ETHERSCAN_V2, params={**params, "chainid": chain_id, "apikey": key}, component="data.chain"
        )
        if isinstance(payload, dict) and payload.get("status") == "0" and payload.get("message") != "OK":
            # NOTOK/No transactions found — не ошибка, а «данных нет»
            logger.debug("etherscan: %s", payload.get("result"))
            return None
        return payload

    async def _txlist(self, chain: str, address: str, *, offset: int = 100, sort: str = "asc") -> list[dict]:
        payload = await self._etherscan(
            chain,
            {"module": "account", "action": "txlist", "address": address, "page": 1, "offset": offset, "sort": sort},
        )
        if isinstance(payload, dict):
            result = payload.get("result")
            if isinstance(result, list):
                return result
        return []

    async def deployer(self, token: TokenCandidate) -> DeployerInfo | None:
        """Собираем профиль деплоера по данным эксплорера."""
        deployer_address = token.extra.get("deployer") if isinstance(token.extra, dict) else None
        if not deployer_address:
            # контракт создан транзакцией: ищем первую internal-транзакцию создания
            payload = await self._etherscan(
                token.chain,
                {
                    "module": "contract",
                    "action": "getcontractcreation",
                    "contractaddresses": token.address,
                },
            )
            result = payload.get("result") if isinstance(payload, dict) else None
            if isinstance(result, list) and result:
                deployer_address = result[0].get("contractCreator")
        if not deployer_address:
            return None

        try:
            first_txs = await self._txlist(token.chain, deployer_address, offset=50, sort="asc")
            recent_txs = await self._txlist(token.chain, deployer_address, offset=100, sort="desc")
        except ProviderUnavailable as exc:
            monitor.record("data.chain.deployer", exc)
            return None
        if not first_txs:
            return None

        first_ts = int(first_txs[0].get("timeStamp") or 0)
        age_days = (now_ms() / 1000 - first_ts) / 86_400 if first_ts else None

        # контракт-креации: у них пустое поле to
        created = [tx for tx in recent_txs if not tx.get("to")]
        funded_by = ""
        for tx in first_txs[:5]:
            if tx.get("to", "").lower() == deployer_address.lower():
                funded_by = str(tx.get("from") or "")
                break

        return DeployerInfo(
            address=str(deployer_address),
            age_days=round(age_days, 1) if age_days is not None else None,
            first_tx_ms=first_ts * 1000 if first_ts else None,
            tx_count=int(recent_txs[0].get("nonce") or 0) or len(recent_txs) or None,
            tokens_deployed=len(created) or None,
            funded_by=funded_by,
            balance_native=None,
            sold_out=None,
            flagged=None,
            source="etherscan-v2",
            is_stub=False,
        )

    async def holders(self, token: TokenCandidate) -> HolderStats | None:
        """Запасной источник холдеров — Moralis (нужен MORALIS_API_KEY)."""
        if not self.config.MORALIS_API_KEY:
            return None
        chain = MORALIS_CHAIN.get(token.chain.lower())
        if not chain:
            return None
        try:
            payload = await self.http.get_json(
                f"{MORALIS}/erc20/{token.address}/holders",
                params={"chain": chain, "limit": 20, "order": "DESC"},
                headers={"X-API-Key": self.config.MORALIS_API_KEY},
                component="data.chain.moralis",
            )
        except ProviderUnavailable as exc:
            monitor.record("data.chain.moralis", exc)
            return None
        holders = (payload or {}).get("result") or []
        percents = sorted((float(h.get("percentage_relative_to_total_supply") or 0.0) for h in holders), reverse=True)
        if not percents:
            return None
        return HolderStats(
            top1_pct=round(percents[0], 2),
            top10_pct=round(sum(percents[:10]), 2),
            holders_count=None,
            source="moralis",
            is_stub=False,
        )

    async def contract_source(self, token: TokenCandidate) -> dict[str, Any]:
        """
        Метаданные верификации контракта — их отдаём AI-модулю на разбор.

        Возвращаем имя контракта, компилятор, признак прокси и (если есть)
        имя функций из ABI. Сам байткод не качаем: для оценки риска достаточно
        сигнатур функций и факта верификации.
        """
        payload = await self._etherscan(
            token.chain,
            {"module": "contract", "action": "getsourcecode", "address": token.address},
        )
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, list) or not result:
            return {}
        item = result[0]
        return {
            "contract_name": item.get("ContractName") or "",
            "compiler": item.get("CompilerVersion") or "",
            "proxy": str(item.get("Proxy")) == "1",
            "implementation": item.get("Implementation") or "",
            "verified": bool(item.get("SourceCode")),
            "abi": item.get("ABI") or "",
        }
