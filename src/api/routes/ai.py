"""
AI Assistant and Natural Language Quantitative Intelligence Endpoints.
"""
from fastapi import APIRouter
from pydantic import BaseModel

from src.ai.assistant import AIAssistant

router = APIRouter(prefix="/api/v1/ai", tags=["AI Quant Analyst"])


class AIQueryRequest(BaseModel):
    query: str


class AIQueryResponse(BaseModel):
    query: str
    response: str


@router.post("/query", response_model=AIQueryResponse)
async def query_ai_analyst(payload: AIQueryRequest):
    answer = await AIAssistant.process_user_query(payload.query)
    return AIQueryResponse(query=payload.query, response=answer)
