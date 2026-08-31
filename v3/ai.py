"""AI reasoning layer (explanation only).

Container: "AI" here means a structured explanation layer that (a) receives the
same features the deterministic engine used, (b) explains the setup in plain
language, (c) lists conflicting/weak factors, and (d) **never** invents market
data or overrides the deterministic gate.

The default ``RuleBasedReasoner`` is deterministic and requires no API key. If
``OPENAI_API_KEY`` is set, ``OpenAIReasoner`` may be used for richer language
output, but even then it only annotates ``reasons``/``risks``; direction,
levels and score are already fixed before AI runs.
"""

from __future__ import annotations

from typing import Any

from v3.models import TradingSignal

DISCLAIMER = "Детерминированная часть скоринга неизменна; AI-слой только объясняет."


class RuleBasedReasoner:
    """Explain a signal from its own structured features."""

    name = "rule-based"

    def explain(self, signal: TradingSignal) -> TradingSignal:
        # The AI must not change market data or levels.
        original_direction = signal.direction
        original_uid = signal.uid
        original_zone = tuple(signal.entry_zone)

        features = signal.features or {}
        views = features.get("timeframes", []) or []
        der = features.get("derivatives", {}) or {}
        of = features.get("orderflow", {}) or {}
        ctx = features.get("context", {}) or {}
        reg = features.get("regime", {}) or {}

        reasons = list(signal.reasons)
        risks = list(signal.risks)

        if signal.direction in ("LONG", "SHORT"):
            # Why
            if reg.get("regime"):
                reasons.append(f"Режим рынка: {reg['regime']}.")
            trend_bits = [f"{v.get('timeframe')}:{v.get('trend')}(ADX {v.get('adx', 0):.0f})" for v in views[:4]]
            if trend_bits:
                reasons.append("Тренды по ТФ: " + ", ".join(trend_bits))
            if der.get("funding_trend") not in (None, "unknown"):
                reasons.append(f"Фандинг: {der.get('funding_trend')} ({der.get('funding_rate')}).")
            if of.get("liquidity_grade") in ("excellent", "ok"):
                reasons.append(
                    f"Ликвидность: {of.get('liquidity_grade')}, "
                    f"перекос стакана {float(of.get('imbalance', 0)):+.2f}."
                )

            # What could go wrong
            conflicts = reg.get("conflicts") or []
            if conflicts:
                risks.append("Конфликт таймфреймов: " + "; ".join(conflicts) + ".")
            if der.get("funding_trend") == "overheated_long" and signal.direction == "LONG":
                risks.append("Фандинг перегрет по лонгам — риск обратного сквиза.")
            if der.get("funding_trend") == "overheated_short" and signal.direction == "SHORT":
                risks.append("Фандинг перегрет по шортам — риск сквиза вверх.")
            if signal.is_demo:
                risks.append("Данные демо — не живой сигнал.")
        else:
            if signal.no_trade_reasons:
                reasons.append(f"NO TRADE: {signal.no_trade_reasons[0]}.")
            reasons.append("Детерминированный gate считает сетап недостаточным.")

        reasons.extend([DISCLAIMER])
        signal.reasons = _dedupe(reasons[:12])
        signal.risks = _dedupe(risks[:10])

        # guards: AI cannot change trade-relevant fields
        signal.direction = original_direction
        signal.uid = original_uid
        signal.entry_zone = original_zone
        return signal

    def __call__(self, signal: TradingSignal) -> TradingSignal:
        return self.explain(signal)


class OpenAIReasoner(RuleBasedReasoner):
    """Optional LLM annotator. Falls back to rule-based on any failure."""

    name = "openai"

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 20.0,
        http=None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        self.http = http

    def explain(self, signal: TradingSignal) -> TradingSignal:
        try:
            import json

            import httpx

            payload = {
                "model": self.model,
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": "You add plain-language explanations for a cryptocurrency futures signal. Reply JSON: {\"reasons\": [...], \"risks\": [...]}. Never invent data."},
                    {"role": "user", "content": json.dumps(signal.to_dict(), ensure_ascii=False)[:10000]},
                ],
            }
            client = self.http or httpx
            if hasattr(client, "post"):
                resp = client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                    timeout=self.timeout,
                )
                content = resp.json()["choices"][0]["message"]["content"]
                data = json.loads(content)
                signal.reasons = _dedupe(list(signal.reasons) + data.get("reasons", []))[:12]
                signal.risks = _dedupe(list(signal.risks) + data.get("risks", []))[:10]
        except Exception:  # noqa: BLE001 -- never let AI break a signal
            return super().explain(signal)
        return signal


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    for v in values:
        if v and v not in out:
            out.append(v)
    return out


def build_reasoner(cfg: Any) -> RuleBasedReasoner:
    """Choose the configured reasoner; default rule-based always available."""
    if getattr(cfg, "OPENAI_API_KEY", ""):
        try:
            return OpenAIReasoner(
                cfg.OPENAI_API_KEY,
                getattr(cfg, "OPENAI_MODEL", "gpt-4o-mini"),
                getattr(cfg, "OPENAI_BASE_URL", "https://api.openai.com/v1"),
                getattr(cfg, "OPENAI_TIMEOUT_SECONDS", 20.0),
            )
        except Exception:  # noqa: BLE001
            return RuleBasedReasoner()
    return RuleBasedReasoner()
