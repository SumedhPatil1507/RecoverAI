"""JWT/RBAC helpers with explicit tenant and role claims."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


ROLE_ADMIN = "enterprise_admin"
ROLE_OPERATOR = "operator"
ROLE_AUDITOR = "auditor"
VALID_ROLES = frozenset({ROLE_ADMIN, ROLE_OPERATOR, ROLE_AUDITOR})


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    tenant_id: str
    role: str


def decode_bearer_token(token: str, secret: str, algorithms: tuple[str, ...] = ("HS256",)) -> Principal:
    """Decode and validate a JWT. PyJWT is an explicit production dependency."""
    if not token or not secret:
        raise ValueError("Bearer token and JWT secret are required")
    try:
        import jwt
        claims: dict[str, Any] = jwt.decode(token, secret, algorithms=list(algorithms), options={"require": ["sub", "tenant_id", "role"]})
    except Exception as exc:
        raise ValueError("Invalid bearer token") from exc
    role = str(claims["role"])
    tenant_id = str(claims["tenant_id"])
    subject = str(claims["sub"])
    if role not in VALID_ROLES or not tenant_id or not subject:
        raise ValueError("Invalid principal claims")
    return Principal(subject=subject, tenant_id=tenant_id, role=role)


def authorize(principal: Principal, *allowed_roles: str) -> None:
    """Raise PermissionError unless the principal has one of the allowed roles."""
    if principal.role not in set(allowed_roles):
        raise PermissionError("Insufficient role for this operation")
