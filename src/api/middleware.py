"""
API Middleware for Request Timing, Observability, and Error Handling.
"""
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.core.logging import get_logger

logger = get_logger("api.middleware")


class ObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        start_time = time.monotonic()
        response = await call_next(request)
        process_time = (time.monotonic() - start_time) * 1000.0

        response.headers["X-Process-Time-Ms"] = f"{process_time:.2f}"
        logger.info(
            "API Request",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round(process_time, 2),
        )
        return response
