"""AI explainer — optional OpenAI layer for human-readable explanations (does not affect signal)."""

from __future__ import annotations

import httpx
from loguru import logger

from ..config import Settings
from ..signals.models import Signal


async def explain_signal_ai(signal: Signal, cfg: Settings) -> str | None:
    if not cfg.AI_ENABLED or not cfg.OPENAI_API_KEY:
        return None

    try:
        prompt = f"""
Ты — крипто-аналитик. Объясни сигнал простыми словами для новичка.

Символ: {signal.symbol} {signal.direction}
Биржа: {signal.exchange}
Вход: {signal.entry}
Стоп: {signal.stop_loss}
Тейки: {signal.take_profits}
R:R: 1:{signal.risk_reward:.1f}
Оценка: {signal.quality_score:.0f} ({signal.quality_grade})
Уверенность: {signal.confidence_pct:.0f}% {signal.confidence_label}
Ожидаемый скачок: {signal.expected_move_pct:.2f}%
Режим: {signal.regime}
Фаза: {signal.early_phase} heat {signal.heat:.0f}
Стакан imbalance: {signal.orderbook_imbalance:.2f}
Детекторы: {', '.join([f\"{d['type']} {d['direction']}\" for d in signal.signals_detected[:3]])}

Дай краткое объяснение (2-3 предложения) почему этот сетап сильный, что подтверждает, и на что обратить внимание. Без финансовых советов, только анализ.
"""

        async with httpx.AsyncClient(timeout=cfg.OPENAI_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                f"{cfg.OPENAI_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {cfg.OPENAI_API_KEY}"},
                json={
                    "model": cfg.OPENAI_MODEL,
                    "messages": [
                        {"role": "system", "content": "Ты — опытный крипто-аналитик, объясняешь сигналы простым языком, без воды, с упором на факты."},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 300,
                    "temperature": 0.6,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                return content.strip()
            else:
                logger.warning(f"AI explain failed {resp.status_code}: {resp.text[:200]}")
                return None
    except Exception as e:
        logger.debug(f"AI explain error: {e}")
        return None
