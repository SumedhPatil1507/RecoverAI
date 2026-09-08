"""
RecoverAI Enterprise – JWT Authentication & RBAC Middleware
============================================================
Three roles, strictly scoped:

  Enterprise Admin  — full access to all endpoints
  Operator/Agent    — HITL queue only (approve/reject/modify)
  Auditor           — read-only audit and stats endpoints

JWT tokens are issued by POST /auth/token and verified on every
protected route via the ``require_role`` FastAPI dependency.

Token format
------------
Standard HS256 JWT with claims:
  sub         merchant_id (tenant identifier)
  role        "admin" | "operator" | "auditor"
  exp         unix timestamp (utcnow + jwt_expire_hours)
  iat         unix timestamp (utcnow)

Environment variables
---------------------
JWT_SECRET_KEY      HMAC-SHA256 key (32-byte hex, stored in Secrets Manager)
JWT_ALGORITHM       default HS256
JWT_EXPIRE_HOURS    default 8
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

_pkg = os.path.dirname(os.path.abspath(__file__))
if _pkg not in sys.path:
    sys.path.insert(0, _pkg)

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)
_bearer = HTTPBearer(auto_error=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Role definitions
# ═══════════════════════════════════════════════════════════════════════════════

class Role(str, Enum):
    ADMIN    = "admin"     # Enterprise admin — full access
    OPERATOR = "operator"  # Agent / HITL operator — HITL endpoints only
    AUDITOR  = "auditor"   # Read-only — audit + stats endpoints


# Route-level permission sets (used by require_role)
_AUDITOR_ALLOWED_PREFIXES = (
    "/api/audit",
    "/api/stats",
    "/api/transactions",
    "/api/ab/results",
    "/api/ml/drift",
    "/health",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
)

_OPERATOR_ALLOWED_PREFIXES = _AUDITOR_ALLOWED_PREFIXES + (
    "/api/hitl",
)


# ═══════════════════════════════════════════════════════════════════════════════
# JWT helpers (PyJWT when available; HMAC fallback otherwise)
# ═══════════════════════════════════════════════════════════════════════════════

def _get_jwt_settings() -> tuple[str, str, int]:
    """Return (secret_key, algorithm, expire_hours) from config."""
    try:
        from config import get_settings
        s = get_settings()
        return (
            getattr(s, "jwt_secret_key", "") or os.getenv("JWT_SECRET_KEY", "dev-jwt-secret-replace"),
            getattr(s, "jwt_algorithm",   "HS256"),
            int(getattr(s, "jwt_expire_hours", 8)),
        )
    except Exception:
        return (
            os.getenv("JWT_SECRET_KEY", "dev-jwt-secret-replace"),
            "HS256",
            8,
        )


def create_token(merchant_id: str, role: Role) -> str:
    """
    Create a signed JWT for the given merchant / role.

    Uses PyJWT when installed; falls back to a minimal HMAC-SHA256
    implementation so the module works without the extra dependency.
    """
    secret, algorithm, expire_hours = _get_jwt_settings()
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub":  merchant_id,
        "role": role.value,
        "iat":  int(now.timestamp()),
        "exp":  int((now + timedelta(hours=expire_hours)).timestamp()),
    }

    try:
        import jwt as _jwt
        return _jwt.encode(payload, secret, algorithm=algorithm)
    except ImportError:
        # Minimal fallback: base64url(header).base64url(payload).HMAC
        import base64, hashlib, hmac as _hmac, json as _json
        def _b64(data: bytes) -> str:
            return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

        header  = _b64(_json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        body    = _b64(_json.dumps(payload).encode())
        msg     = f"{header}.{body}".encode()
        sig     = _b64(_hmac.new(secret.encode(), msg, hashlib.sha256).digest())
        return f"{header}.{body}.{sig}"


def decode_token(token: str) -> dict[str, Any]:
    """
    Decode and verify a JWT.  Raises ValueError on any failure.
    """
    secret, algorithm, _ = _get_jwt_settings()

    try:
        import jwt as _jwt
        try:
            return _jwt.decode(token, secret, algorithms=[algorithm])
        except _jwt.ExpiredSignatureError:
            raise ValueError("Token has expired")
        except _jwt.InvalidTokenError as exc:
            raise ValueError(f"Invalid token: {exc}") from exc
    except ImportError:
        pass

    # Minimal fallback verifier
    import base64, hashlib, hmac as _hmac, json as _json, time

    def _b64dec(s: str) -> bytes:
        padding = 4 - len(s) % 4
        return base64.urlsafe_b64decode(s + "=" * padding)

    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Malformed token")

    header_b, body_b, sig_b = parts
    msg      = f"{header_b}.{body_b}".encode()
    expected = base64.urlsafe_b64encode(
        _hmac.new(secret.encode(), msg, hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    if not _hmac.compare_digest(expected, sig_b):
        raise ValueError("Signature verification failed")

    payload = _json.loads(_b64dec(body_b))
    if payload.get("exp", 0) < time.time():
        raise ValueError("Token has expired")

    return payload


# ═══════════════════════════════════════════════════════════════════════════════
# FastAPI dependencies
# ═══════════════════════════════════════════════════════════════════════════════

class TokenData:
    __slots__ = ("merchant_id", "role")

    def __init__(self, merchant_id: str, role: Role) -> None:
        self.merchant_id = merchant_id
        self.role        = role


def _extract_token_data(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> TokenData:
    """
    Parse and validate the Bearer token.
    Raises HTTP 401 on any failure.
    """
    try:
        payload = decode_token(credentials.credentials)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )

    merchant_id = payload.get("sub", "")
    role_raw    = payload.get("role", "")

    try:
        role = Role(role_raw)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Unknown role: {role_raw!r}",
        )

    return TokenData(merchant_id=merchant_id, role=role)


def require_role(*allowed_roles: Role):
    """
    FastAPI dependency factory.

    Usage::
        @app.get("/api/hitl/queue")
        async def hitl_queue(
            token: TokenData = Depends(require_role(Role.ADMIN, Role.OPERATOR))
        ):
            ...
    """
    def _check(
        token_data: TokenData = Depends(_extract_token_data),
    ) -> TokenData:
        if token_data.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Role '{token_data.role.value}' is not authorised. "
                    f"Required: {[r.value for r in allowed_roles]}"
                ),
            )
        return token_data
    return _check


# Convenience shorthands
require_admin    = require_role(Role.ADMIN)
require_operator = require_role(Role.ADMIN, Role.OPERATOR)
require_auditor  = require_role(Role.ADMIN, Role.OPERATOR, Role.AUDITOR)


# ═══════════════════════════════════════════════════════════════════════════════
# /auth/token endpoint  (registered in main.py)
# ═══════════════════════════════════════════════════════════════════════════════

from pydantic import BaseModel


class TokenRequest(BaseModel):
    merchant_id: str
    api_key:     str
    role:        str = "admin"


class TokenResponse(BaseModel):
    access_token: str
    token_type:   str = "bearer"
    expires_in:   int  # seconds
    merchant_id:  str
    role:         str


def issue_token(req: TokenRequest) -> TokenResponse:
    """
    Issue a JWT for a merchant.  In production, validate `api_key` against
    a secrets store; here we verify against TENANT_API_KEYS env var.

    Format: TENANT_API_KEYS = "mid1:key1,mid2:key2"
    """
    raw_keys = os.getenv("TENANT_API_KEYS", "")
    tenant_keys: dict[str, str] = {}
    for pair in raw_keys.split(","):
        if ":" in pair:
            mid, key = pair.split(":", 1)
            tenant_keys[mid.strip()] = key.strip()

    # In dev mode (no tenant keys configured) any api_key is accepted
    if tenant_keys:
        expected = tenant_keys.get(req.merchant_id, "")
        import hmac as _hmac
        if not expected or not _hmac.compare_digest(expected, req.api_key):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid merchant_id or api_key",
            )

    try:
        role = Role(req.role.lower())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role: {req.role!r}. Must be admin, operator, or auditor.",
        )

    _, _, expire_hours = _get_jwt_settings()
    token = create_token(req.merchant_id, role)

    return TokenResponse(
        access_token=token,
        expires_in=expire_hours * 3600,
        merchant_id=req.merchant_id,
        role=role.value,
    )
