"""Server-side page access control (Harvey-style per-page permissions).

The role portals are static files, so a direct ``GET /ui/judge.html`` would
otherwise be served to anyone. This middleware sits in front of the static mount
and, for every page listed in :data:`app.core.permissions.PAGE_PERMISSIONS`,
resolves the session from the HttpOnly cookie and checks the page's permission
*before* the file is served.

A caller with no session is redirected to the login page; a caller whose role
does not unlock the page is redirected to a "forbidden" page. The permission
registry is the single source of truth, so the page gate, the client-side guard
and the admin matrix can never drift apart.
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import Request
from fastapi.responses import RedirectResponse, Response

from app.core.config import settings
from app.core.logging import get_logger
from app.core.permissions import can_open_page, page_permission
from app.services.auth import auth_service

logger = get_logger(__name__)

#: HttpOnly cookie that carries the session to the static pages. The API keeps
#: using the bearer token; the cookie only lets a page request authenticate
#: itself without JavaScript having to run first.
SESSION_COOKIE = "legal_session"

#: Where an unauthenticated visitor is sent, and where a forbidden one goes.
LOGIN_PAGE = "/ui/login.html"
FORBIDDEN_PAGE = "/ui/forbidden.html"


def _is_page_request(path: str) -> bool:
    """True for an HTML page the gate should consider (not an asset or the API)."""
    return path.startswith("/ui/") and path.endswith(".html")


async def _resolve_user(request: Request) -> dict | None:
    """The signed-in user for this request, or ``None`` when there is no session.

    A browser carries the HttpOnly cookie; an API client or a test may send the
    bearer token instead, so both are accepted.
    """
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        authorization = request.headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            token = authorization.split(" ", 1)[1].strip()
    if not token:
        return None
    try:
        return await auth_service.user_from_token(token)
    except Exception:
        # An expired or tampered credential is treated as "not signed in".
        return None


def set_session_cookie(response: Response, token: str) -> None:
    """Attach the session cookie to a login/signup response."""
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=settings.access_token_expire_minutes * 60,
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


async def page_gate(request: Request, call_next):
    """Deny a gated page before the static mount can serve it."""
    path = request.url.path
    if not _is_page_request(path) or page_permission(path) is None:
        return await call_next(request)

    user = await _resolve_user(request)
    if user is None:
        target = f"{LOGIN_PAGE}?next={quote(path)}"
        return RedirectResponse(target, status_code=302)
    if not can_open_page(user, path):
        logger.info(
            "page access denied",
            extra={"username": user.get("username"), "role": user.get("role"), "path": path},
        )
        return RedirectResponse(FORBIDDEN_PAGE, status_code=302)
    return await call_next(request)
