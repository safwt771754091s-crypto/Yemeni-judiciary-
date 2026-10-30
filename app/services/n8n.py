"""n8n integration.

Two directions of flow:

* Outbound (this module) - after a judgment is produced, POST a compact payload
  to an n8n webhook so n8n can route it to review queues, e-mail, e-signature,
  archiving, etc. Requests are HMAC-SHA256 signed (``X-Legal-Signature``) so the
  n8n side can verify authenticity.
* Inbound - n8n calls the normal REST API (``/api/v1/cases/submit``); the
  optional ``X-N8N-Secret`` header is checked when ``N8N_HMAC_SECRET`` is set.

Delivery is best-effort: a failed webhook never fails the API request.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class N8NClient:
    def __init__(self) -> None:
        self.enabled = bool(settings.n8n_enabled and settings.n8n_webhook_url)
        self.url = settings.n8n_webhook_url
        self.secret = settings.n8n_hmac_secret

    def sign(self, body: bytes) -> str:
        if not self.secret:
            return ""
        return hmac.new(self.secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    def verify(self, body: bytes, signature: str) -> bool:
        if not self.secret:
            return True
        expected = self.sign(body)
        return hmac.compare_digest(expected, signature or "")

    async def emit(self, event: str, payload: dict[str, Any]) -> bool:
        if not self.enabled:
            logger.debug("n8n disabled; skipping event", extra={"event": event})
            return False
        body = json.dumps(
            {"event": event, "sent_at": time.time(), "data": payload},
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
        headers = {"Content-Type": "application/json", "X-Legal-Event": event}
        signature = self.sign(body)
        if signature:
            headers["X-Legal-Signature"] = signature
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(self.url, content=body, headers=headers)
                response.raise_for_status()
            logger.info("n8n event delivered", extra={"event": event})
            return True
        except Exception as exc:
            logger.warning("n8n delivery failed", extra={"event": event, "error": str(exc)})
            return False

    async def emit_judgment(self, case_id: str, envelope: dict[str, Any]) -> bool:
        return await self.emit(
            "judgment.created",
            {
                "case_id": case_id,
                "grounding_score": envelope.get("grounding_score"),
                "confidence": envelope.get("confidence"),
                "warnings": envelope.get("warnings", []),
                "citations": envelope.get("citations", []),
                "verdict": (envelope.get("draft") or {}).get("verdict"),
            },
        )


n8n_client = N8NClient()
