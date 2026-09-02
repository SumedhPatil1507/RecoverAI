"""
RecoverAI Enterprise – Security Layer
======================================
Responsibilities:
  1. PII Redaction      — recursive dict/list walk, field-name + regex masking
  2. HMAC Verification  — constant-time Razorpay webhook signature check
  3. AES-256-GCM        — encrypt / decrypt sensitive DB column values at rest
  4. FastAPI dependency — ``signature_required`` for webhook endpoints

AES-256-GCM Encryption
-----------------------
Sensitive DB columns (e.g. email_redacted, card details) can be stored
encrypted using ``encrypt_value`` / ``decrypt_value``.

Key derivation:
  COLUMN_ENCRYPTION_KEY env var  →  32-byte hex string (64 chars)
  Fallback: AUDIT_HMAC_KEY       →  stretched via HKDF-SHA256 to 32 bytes
  Fallback: RAZORPAY_WEBHOOK_SECRET → same HKDF stretch

Format on disk: ``aes256gcm:{b64(nonce + ciphertext + tag)}``
The 12-byte nonce is randomly generated per encryption; the 16-byte GCM
authentication tag is appended to the ciphertext by Python's ``cryptography``
library.

When ``cryptography`` is not installed the functions fall back to a
base64-only encoding (still marked with the ``b64only:`` prefix) so the
app remains functional without hard dependencies.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import re
import struct
from typing import Any

from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)

# ── PII field names that should always be redacted ───────────────────────────
_PII_FIELDS: frozenset[str] = frozenset({
    "card_number", "cardnumber", "card_no", "pan",
    "email", "email_id", "email_address",
    "phone", "phone_number", "mobile", "contact",
    "cvv", "cvc", "expiry", "expiry_date",
    "account_number", "bank_account", "ifsc",
    "name", "customer_name", "billing_name",
    "address", "billing_address",
    "vpa", "upi_id",
})

_CARD_RE  = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_PHONE_RE = re.compile(r"(\+?91[\-\s]?)?[6-9]\d{9}")


# ── PII masking helpers ───────────────────────────────────────────────────────

def _mask_card(v: str) -> str:
    d = re.sub(r"\D", "", v)
    return f"**** **** **** {d[-4:]}" if len(d) >= 12 else "****"


def _mask_email(v: str) -> str:
    m = _EMAIL_RE.search(v)
    if not m:
        return "***@***.***"
    local, domain = m.group().split("@", 1)
    masked = (local[0] + "*" * (len(local) - 2) + local[-1]
              if len(local) > 2 else "*" * len(local))
    return f"{masked}@{domain}"


def _mask_phone(v: str) -> str:
    d = re.sub(r"\D", "", v)
    return f"{'*' * (len(d) - 4)}{d[-4:]}" if len(d) >= 4 else "****"


def _redact_str(v: str, field: str) -> str:
    f = field.lower()
    if any(c in f for c in ("card", "pan", "cvv", "cvc")):
        return _mask_card(v)
    if "email" in f:
        return _mask_email(v)
    if any(c in f for c in ("phone", "mobile", "contact")):
        return _mask_phone(v)
    # Pattern-based fallback on the value itself
    if _CARD_RE.search(v):
        return _CARD_RE.sub(lambda m: _mask_card(m.group()), v)
    if _EMAIL_RE.search(v):
        return _EMAIL_RE.sub(lambda m: _mask_email(m.group()), v)
    if _PHONE_RE.search(v):
        return _PHONE_RE.sub(lambda m: _mask_phone(m.group()), v)
    return "***REDACTED***"


def redact_pii(payload: Any, _field: str = "") -> Any:
    """
    Recursively redact PII from a dict / list / str payload.

    Safe to call on arbitrary Razorpay webhook payloads before persisting
    or passing to LLM prompt nodes.
    """
    if isinstance(payload, dict):
        return {
            k: _redact_str(str(v), k) if (k.lower() in _PII_FIELDS and v) else redact_pii(v, k)
            for k, v in payload.items()
        }
    if isinstance(payload, list):
        return [redact_pii(item, _field) for item in payload]
    if isinstance(payload, str) and _field.lower() in _PII_FIELDS:
        return _redact_str(payload, _field)
    return payload


# ── HMAC webhook verification ─────────────────────────────────────────────────

def verify_razorpay_signature(payload_bytes: bytes, signature: str,
                               secret: str) -> bool:
    """Constant-time HMAC-SHA256 verification for Razorpay webhooks."""
    try:
        expected = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature.lower())
    except Exception:
        return False


async def signature_required(request: Request) -> bytes:
    """
    FastAPI dependency — verifies ``X-Razorpay-Signature`` before allowing
    the request to proceed.  Returns the raw body bytes on success.
    """
    from config import get_settings
    settings = get_settings()

    raw: bytes = await request.body()
    sig        = request.headers.get("X-Razorpay-Signature", "")

    if not sig:
        logger.warning("SECURITY: missing signature from %s",
                       getattr(request.client, "host", "unknown"))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Missing webhook signature.")

    if not verify_razorpay_signature(raw, sig, settings.razorpay_webhook_secret):
        logger.warning("SECURITY: invalid signature from %s",
                       getattr(request.client, "host", "unknown"))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid webhook signature.")

    return raw


# ═══════════════════════════════════════════════════════════════════════════════
# AES-256-GCM column encryption
# ═══════════════════════════════════════════════════════════════════════════════

_AES_PREFIX    = "aes256gcm:"
_B64_PREFIX    = "b64only:"
_NONCE_BYTES   = 12   # 96-bit nonce for GCM
_TAG_BYTES     = 16   # 128-bit authentication tag


def _derive_column_key() -> bytes:
    """
    Return a 32-byte AES key.

    Priority:
      1. COLUMN_ENCRYPTION_KEY   hex string (64 chars)
      2. AUDIT_HMAC_KEY          stretched via HKDF-SHA256
      3. RAZORPAY_WEBHOOK_SECRET stretched via HKDF-SHA256
    """
    raw = os.getenv("COLUMN_ENCRYPTION_KEY", "")
    if raw and len(raw) == 64:
        try:
            return bytes.fromhex(raw)
        except ValueError:
            pass

    # HKDF stretch
    source = (
        os.getenv("AUDIT_HMAC_KEY")
        or os.getenv("RAZORPAY_WEBHOOK_SECRET")
        or "insecure-dev-key-replace-in-prod"
    )
    try:
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from cryptography.hazmat.primitives import hashes
        hkdf = HKDF(algorithm=hashes.SHA256(), length=32,
                    salt=b"recoverai-column-enc",
                    info=b"aes256gcm-column-key")
        return hkdf.derive(source.encode())
    except ImportError:
        # cryptography not installed — plain SHA-256 stretch (less secure but functional)
        return hashlib.sha256(source.encode()).digest()


def encrypt_value(plaintext: str) -> str:
    """
    Encrypt a string column value with AES-256-GCM.

    Returns a prefixed base64 string safe for storage in SQLite / Postgres.
    Format: ``aes256gcm:{base64(12-byte nonce || ciphertext || 16-byte tag)}``

    Falls back to ``b64only:{base64}`` when the ``cryptography`` package is
    not installed (development / CI environments without the C extension).
    """
    if not plaintext:
        return plaintext

    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        key   = _derive_column_key()
        nonce = os.urandom(_NONCE_BYTES)
        ct    = AESGCM(key).encrypt(nonce, plaintext.encode(), None)  # ct includes tag
        blob  = base64.b64encode(nonce + ct).decode()
        return f"{_AES_PREFIX}{blob}"
    except ImportError:
        # Graceful fallback: base64 only (no confidentiality, but pipeline runs)
        blob = base64.b64encode(plaintext.encode()).decode()
        logger.debug("encrypt_value: cryptography not installed — b64 fallback")
        return f"{_B64_PREFIX}{blob}"
    except Exception as exc:
        logger.error("encrypt_value failed: %s", exc)
        return plaintext


def decrypt_value(ciphertext: str) -> str:
    """
    Decrypt a value previously encrypted with ``encrypt_value``.

    Handles both ``aes256gcm:`` and ``b64only:`` prefixes.
    Returns the original plaintext unmodified if the value is not prefixed
    (backwards-compatible with unencrypted rows).
    """
    if not ciphertext:
        return ciphertext

    if ciphertext.startswith(_B64_PREFIX):
        try:
            return base64.b64decode(ciphertext[len(_B64_PREFIX):]).decode()
        except Exception:
            return ciphertext

    if ciphertext.startswith(_AES_PREFIX):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            blob  = base64.b64decode(ciphertext[len(_AES_PREFIX):])
            nonce = blob[:_NONCE_BYTES]
            ct    = blob[_NONCE_BYTES:]
            key   = _derive_column_key()
            return AESGCM(key).decrypt(nonce, ct, None).decode()
        except Exception as exc:
            logger.error("decrypt_value failed: %s", exc)
            return ciphertext

    # Unencrypted legacy value — return as-is
    return ciphertext


def is_encrypted(value: str) -> bool:
    """Return True if the value was encrypted by ``encrypt_value``."""
    return value.startswith(_AES_PREFIX) or value.startswith(_B64_PREFIX)
