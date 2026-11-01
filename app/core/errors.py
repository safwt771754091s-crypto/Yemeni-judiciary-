"""Domain exceptions and FastAPI exception handlers."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class LegalAIError(Exception):
    """Base error for the platform."""

    status_code = 400
    code = "legal_ai_error"

    def __init__(self, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class NotFoundError(LegalAIError):
    status_code = 404
    code = "not_found"


class ValidationError(LegalAIError):
    status_code = 422
    code = "validation_error"


class DatabaseUnavailableError(LegalAIError):
    status_code = 503
    code = "database_unavailable"


class LLMError(LegalAIError):
    status_code = 502
    code = "llm_error"


class IngestionError(LegalAIError):
    status_code = 400
    code = "ingestion_error"


class GroundingError(LegalAIError):
    """Raised when generated legal reasoning cannot be grounded in real statutes."""

    status_code = 422
    code = "grounding_error"


class UnauthorizedError(LegalAIError):
    """Raised when a request carries no valid credentials."""

    status_code = 401
    code = "unauthorized"


class ForbiddenError(LegalAIError):
    """Raised when an authenticated user lacks the required role."""

    status_code = 403
    code = "forbidden"


class ConflictError(LegalAIError):
    """Raised when a unique constraint or duplicate resource is detected."""

    status_code = 409
    code = "conflict"


def _payload(code: str, message: str, details: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    return body


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(LegalAIError)
    async def _legal_ai(_: Request, exc: LegalAIError) -> JSONResponse:
        return JSONResponse(
            content=_payload(exc.code, exc.message, exc.details),
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # ``exc.errors()`` may embed non-serialisable objects (e.g. the ValueError
        # raised by a custom validator), so fall back to a sanitised view.
        try:
            details = jsonable_encoder(exc.errors())
        except Exception:
            details = [
                {"type": err.get("type"), "loc": list(err.get("loc", [])), "msg": err.get("msg")}
                for err in exc.errors()
            ]
        return JSONResponse(
            content=_payload("request_validation_error", "البيانات المُرسلة غير صحيحة", details),
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            content=_payload("internal_error", "حدث خطأ غير متوقع في الخادم", str(exc)),
            status_code=500,
        )
