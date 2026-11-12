"""Password hashing and JWT signing, built on the standard library only.

The platform runs offline in tests and in the sandbox, so it must not depend on
a hosted identity provider or on a compiled crypto package. PBKDF2-HMAC-SHA256
covers password storage and HMAC-SHA256 covers token signing; both are in
``hashlib``/``hmac`` and are safe for this threat model (a self-hosted legal
database behind a single court's network).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from app.core.errors import UnauthorizedError, ValidationError

_ALGORITHM = "HS256"
_PBKDF2_ROUNDS = 240_000
_SALT_BYTES = 16


# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #
def hash_password(password: str) -> str:
    """Return a ``pbkdf2_sha256$rounds$salt$hash`` string safe to store."""
    if not password or len(password) < 8:
        raise ValidationError("كلمة المرور يجب أن تكون 8 أحرف على الأقل")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return "$".join(
        [
            "pbkdf2_sha256",
            str(_PBKDF2_ROUNDS),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of a password against a stored hash."""
    try:
        algorithm, rounds, salt_b64, digest_b64 = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(rounds))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def validate_password_strength(password: str) -> None:
    """Raise :class:`ValidationError` when a password is too weak.

    Shared by signup, the owner's user-admin forms and password reset so the
    policy can never drift between the paths that set a password.
    """
    if len(password) < 8:
        raise ValidationError("كلمة المرور يجب أن تكون 8 أحرف على الأقل")
    if password.isdigit() or password.isalpha():
        raise ValidationError("كلمة المرور يجب أن تجمع بين الحروف والأرقام")


def random_token(nbytes: int = 32) -> str:
    """A URL-safe, high-entropy token for reset and verification links."""
    return secrets.token_urlsafe(nbytes)


# --------------------------------------------------------------------------- #
# Tokens
# --------------------------------------------------------------------------- #
def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64url(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def encode_token(
    payload: dict[str, Any],
    secret: str,
    *,
    expires_in: int,
    algorithm: str = _ALGORITHM,
) -> str:
    """Sign ``payload`` as a compact JWT."""
    if algorithm != _ALGORITHM:
        raise ValidationError(f"خوارزمية التوقيع غير مدعومة: {algorithm}")
    header = {"alg": _ALGORITHM, "typ": "JWT"}
    now = int(time.time())
    claims = {**payload, "iat": now, "exp": now + expires_in, "jti": secrets.token_hex(8)}
    signing_input = f"{_b64url(json.dumps(header, separators=(',', ':')).encode())}." \
                    f"{_b64url(json.dumps(claims, separators=(',', ':'), ensure_ascii=False).encode())}"
    signature = hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256)
    return f"{signing_input}.{_b64url(signature.digest())}"


def decode_token(token: str, secret: str) -> dict[str, Any]:
    """Verify a token's signature and expiry, then return its claims."""
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
    except ValueError as exc:
        raise UnauthorizedError("رمز الدخول غير صالح") from exc

    signing_input = f"{header_b64}.{payload_b64}"
    expected = hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256)
    try:
        provided = _unb64url(signature_b64)
    except (ValueError, TypeError) as exc:
        raise UnauthorizedError("رمز الدخول غير صالح") from exc
    if not hmac.compare_digest(expected.digest(), provided):
        raise UnauthorizedError("توقيع رمز الدخول غير صحيح")

    try:
        claims = json.loads(_unb64url(payload_b64))
    except (ValueError, TypeError) as exc:
        raise UnauthorizedError("رمز الدخول غير صالح") from exc

    if int(claims.get("exp", 0)) < int(time.time()):
        raise UnauthorizedError("انتهت صلاحية رمز الدخول، يرجى تسجيل الدخول مجدداً")
    return claims
