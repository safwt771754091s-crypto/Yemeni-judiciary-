"""Outgoing mail for account self-service (verification, password reset).

Three backends behind one interface:

* ``console`` (default) - logs the message, including any link, so the platform
  works end-to-end with no mail server. The link is also returned to the caller
  in development so a reset can be tested without reading the logs.
* ``smtp`` - standard library ``smtplib``/``email``, so no extra dependency.
* ``resend`` - the Resend HTTPS API through ``httpx``.

Every network call is bounded by ``settings.mail_timeout_seconds`` so a slow or
unreachable mail server fails instead of hanging the request (the automation
timeout was caused by an unbounded command; the same rule applies here).
"""
from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Any

import httpx

from app.core.config import settings
from app.core.errors import ValidationError
from app.core.logging import get_logger

logger = get_logger(__name__)

RESEND_ENDPOINT = "https://api.resend.com/emails"


class Mailer:
    """Send a plain-text (optionally HTML) message through the configured backend."""

    @property
    def provider(self) -> str:
        return settings.mail_provider

    async def send(
        self,
        *,
        to: str,
        subject: str,
        text: str,
        html: str | None = None,
    ) -> dict[str, Any]:
        """Deliver one message. Returns ``{"provider", "to", "delivered"}``."""
        if not to:
            raise ValidationError("لا يوجد بريد إلكتروني للمستلم")
        if self.provider == "console":
            # Visible in the server log; never includes a real credential.
            logger.info(
                "outgoing mail (console provider)",
                extra={"to": to, "subject": subject, "body": text},
            )
            return {"provider": "console", "to": to, "delivered": False}
        if self.provider == "smtp":
            await self._send_smtp(to=to, subject=subject, text=text, html=html)
            return {"provider": "smtp", "to": to, "delivered": True}
        if self.provider == "resend":
            await self._send_resend(to=to, subject=subject, text=text, html=html)
            return {"provider": "resend", "to": to, "delivered": True}
        raise ValidationError(f"مزوّد البريد غير مدعوم: {self.provider}")

    async def _send_resend(
        self, *, to: str, subject: str, text: str, html: str | None
    ) -> None:
        if not settings.resend_api_key:
            raise ValidationError("RESEND_API_KEY غير مضبوط")
        payload: dict[str, Any] = {
            "from": f"{settings.mail_from_name} <{settings.mail_from}>",
            "to": [to],
            "subject": subject,
            "text": text,
        }
        if html:
            payload["html"] = html
        async with httpx.AsyncClient(timeout=settings.mail_timeout_seconds) as client:
            response = await client.post(
                RESEND_ENDPOINT,
                json=payload,
                headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            )
        if response.status_code >= 400:
            logger.warning(
                "resend rejected the message",
                extra={"status": response.status_code, "body": response.text[:300]},
            )
            raise ValidationError("تعذّر إرسال البريد الإلكتروني، يرجى المحاولة لاحقاً")

    async def _send_smtp(
        self, *, to: str, subject: str, text: str, html: str | None
    ) -> None:
        if not settings.smtp_host:
            raise ValidationError("SMTP_HOST غير مضبوط")
        message = EmailMessage()
        message["From"] = f"{settings.mail_from_name} <{settings.mail_from}>"
        message["To"] = to
        message["Subject"] = subject
        message.set_content(text)
        if html:
            message.add_alternative(html, subtype="html")
        # smtplib is blocking; the timeout bounds the socket so a dead server
        # raises instead of stalling the worker.
        timeout = settings.mail_timeout_seconds
        if settings.smtp_use_tls:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=timeout) as server:
                server.starttls()
                if settings.smtp_username:
                    server.login(settings.smtp_username, settings.smtp_password or "")
                server.send_message(message)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=timeout) as server:
                if settings.smtp_username:
                    server.login(settings.smtp_username, settings.smtp_password or "")
                server.send_message(message)


mailer = Mailer()
