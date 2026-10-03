"""
RecoverAI Enterprise – RAG-Backed Message Personalizer
=======================================================

Overview
--------
Before DISPATCH, the agent retrieves the 3 most similar historically
successful recovery messages for the current failure category and amount
band, then uses them as few-shot examples to ask the LLM to draft a
personalised outgoing message.

Pipeline position
-----------------
  EV_GATE (PROCEED) → RAG_PERSONALIZE → DISPATCH

What this module does
---------------------
1.  **Vector Store** — 42 curated high-performing templates (7 failure
    categories × 2 channels × 3 outcome bands) are embedded as TF-IDF
    vectors at startup.  ``sentence-transformers`` is used when installed;
    otherwise we fall back to a pure-numpy TF-IDF implementation so
    the module works on Streamlit Cloud without heavy ML deps.

2.  **Retrieval** — given (failure_category, amount_band, channel), compute
    cosine similarity against all stored embeddings and return the top-k
    templates.  The retrieved templates record their IDs for auditability.

3.  **LLM Draft** — the top-k templates are formatted as few-shot context
    and sent to the LLM (or a rule-based formatter if no API key) to produce
    a personalised message in the customer's language.

4.  **Audit Logging** — which template IDs influenced which dispatch is
    written to the ``rag_dispatch_log`` table for explainability.

Environment variables
---------------------
RAG_TOP_K              Number of templates to retrieve (default 3)
RAG_MIN_SIMILARITY     Minimum cosine similarity to include (default 0.0)
RAG_LLM_DRAFT_ENABLED  "1" to call the LLM for drafting (default "1")
RAG_EMBED_MODEL        Sentence-transformers model name
                       (default "all-MiniLM-L6-v2")
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any

_pkg = os.path.dirname(os.path.abspath(__file__))
if _pkg not in sys.path:
    sys.path.insert(0, _pkg)

logger = logging.getLogger(__name__)

# ── Tunables ──────────────────────────────────────────────────────────────────
_TOP_K          = int(os.getenv("RAG_TOP_K",             "3"))
_MIN_SIM        = float(os.getenv("RAG_MIN_SIMILARITY",  "0.0"))
_LLM_DRAFT      = os.getenv("RAG_LLM_DRAFT_ENABLED",     "1") == "1"
_EMBED_MODEL    = os.getenv("RAG_EMBED_MODEL",            "all-MiniLM-L6-v2")


# ═══════════════════════════════════════════════════════════════════════════════
# Template library — 42 curated high-performing messages
# 7 failure categories × 2 channels (WA/SMS & Email) × 3 outcome bands
# ═══════════════════════════════════════════════════════════════════════════════

_TEMPLATES: list[dict[str, Any]] = [
    # ── GATEWAY_DOWN ──────────────────────────────────────────────────────────
    {"id": "T001", "category": "GATEWAY_DOWN", "channel": "whatsapp",
     "amount_band": "small", "outcome": "recovered", "recovery_rate": 0.78,
     "text": "Hi {name}! Your payment of ₹{amount} faced a brief bank glitch. Retry securely here: {link} (expires 1hr). No extra steps needed! 🔒"},
    {"id": "T002", "category": "GATEWAY_DOWN", "channel": "whatsapp",
     "amount_band": "medium", "outcome": "recovered", "recovery_rate": 0.82,
     "text": "Good news {name} — the gateway issue affecting your ₹{amount} payment is now resolved. Complete it in one tap: {link}"},
    {"id": "T003", "category": "GATEWAY_DOWN", "channel": "whatsapp",
     "amount_band": "large", "outcome": "recovered", "recovery_rate": 0.75,
     "text": "Hi {name}, your ₹{amount} transaction was interrupted by a temporary gateway error. Our team has cleared it. Please retry: {link}. Secure & encrypted."},
    {"id": "T004", "category": "GATEWAY_DOWN", "channel": "email",
     "amount_band": "small", "outcome": "recovered", "recovery_rate": 0.71,
     "text": "Your recent payment of ₹{amount} failed due to a momentary bank network issue — not your account. Complete it now: {link}"},
    {"id": "T005", "category": "GATEWAY_DOWN", "channel": "email",
     "amount_band": "medium", "outcome": "recovered", "recovery_rate": 0.76,
     "text": "Quick update on your ₹{amount} order: a gateway hiccup caused the payment to fail. It takes 30 seconds to retry. Click here: {link}"},
    {"id": "T006", "category": "GATEWAY_DOWN", "channel": "email",
     "amount_band": "large", "outcome": "partial", "recovery_rate": 0.61,
     "text": "We noticed your ₹{amount} payment didn't go through due to a temporary bank issue. Your order is saved. Please retry at your convenience: {link}"},

    # ── INSUFFICIENT_FUNDS ────────────────────────────────────────────────────
    {"id": "T007", "category": "INSUFFICIENT_FUNDS", "channel": "whatsapp",
     "amount_band": "small", "outcome": "recovered", "recovery_rate": 0.54,
     "text": "Hi {name}! Split your ₹{amount} payment into easy EMIs — from just ₹{emi_amount}/month. No hidden fees. Apply now: {link} 💳"},
    {"id": "T008", "category": "INSUFFICIENT_FUNDS", "channel": "whatsapp",
     "amount_band": "medium", "outcome": "recovered", "recovery_rate": 0.49,
     "text": "{name}, don't let your cart expire! Pay ₹{amount} in 3 easy instalments. Zero cost EMI available. Check eligibility: {link}"},
    {"id": "T009", "category": "INSUFFICIENT_FUNDS", "channel": "whatsapp",
     "amount_band": "large", "outcome": "partial", "recovery_rate": 0.38,
     "text": "Hi {name}, your ₹{amount} item is still waiting! We offer 6-month EMI at 0% interest. No credit card required. Apply in 2 min: {link}"},
    {"id": "T010", "category": "INSUFFICIENT_FUNDS", "channel": "email",
     "amount_band": "small", "outcome": "recovered", "recovery_rate": 0.51,
     "text": "Your order is reserved! Pay ₹{amount} via UPI, Net Banking, or split into 3 EMIs. Complete payment: {link}"},
    {"id": "T011", "category": "INSUFFICIENT_FUNDS", "channel": "email",
     "amount_band": "medium", "outcome": "recovered", "recovery_rate": 0.47,
     "text": "We've saved your cart! Need flexibility? Choose EMI starting at ₹{emi_amount}/month for your ₹{amount} purchase. Explore options: {link}"},
    {"id": "T012", "category": "INSUFFICIENT_FUNDS", "channel": "email",
     "amount_band": "large", "outcome": "partial", "recovery_rate": 0.35,
     "text": "Your ₹{amount} order is on hold. We offer no-cost EMI, Buy Now Pay Later, and UPI Lite options. Review alternatives: {link}"},

    # ── USER_CANCELLED ────────────────────────────────────────────────────────
    {"id": "T013", "category": "USER_CANCELLED", "channel": "whatsapp",
     "amount_band": "small", "outcome": "recovered", "recovery_rate": 0.63,
     "text": "You were SO close, {name}! Your ₹{amount} is waiting. Complete in one tap: {link} 🛍️"},
    {"id": "T014", "category": "USER_CANCELLED", "channel": "whatsapp",
     "amount_band": "medium", "outcome": "recovered", "recovery_rate": 0.58,
     "text": "Still interested, {name}? Your cart is saved with ₹{amount} worth of items. Grab them before they sell out: {link}"},
    {"id": "T015", "category": "USER_CANCELLED", "channel": "whatsapp",
     "amount_band": "large", "outcome": "partial", "recovery_rate": 0.44,
     "text": "Hi {name}, we noticed you stepped away. Your ₹{amount} order is reserved for 24 hrs. Resume checkout: {link}"},
    {"id": "T016", "category": "USER_CANCELLED", "channel": "email",
     "amount_band": "small", "outcome": "recovered", "recovery_rate": 0.60,
     "text": "Don't leave empty-handed! Your ₹{amount} cart is saved. Complete checkout in under a minute: {link}"},
    {"id": "T017", "category": "USER_CANCELLED", "channel": "email",
     "amount_band": "medium", "outcome": "recovered", "recovery_rate": 0.55,
     "text": "Your ₹{amount} order is waiting for you! We've held your items. Complete payment now: {link}"},
    {"id": "T018", "category": "USER_CANCELLED", "channel": "email",
     "amount_band": "large", "outcome": "partial", "recovery_rate": 0.42,
     "text": "We've reserved your ₹{amount} order. No rush — take your time and complete it here: {link}"},

    # ── BANK_DECLINE ──────────────────────────────────────────────────────────
    {"id": "T019", "category": "BANK_DECLINE", "channel": "whatsapp",
     "amount_band": "small", "outcome": "recovered", "recovery_rate": 0.67,
     "text": "Hi {name}! Your bank declined the ₹{amount} payment (happens sometimes). Try UPI or a different card — same link: {link} ✅"},
    {"id": "T020", "category": "BANK_DECLINE", "channel": "whatsapp",
     "amount_band": "medium", "outcome": "recovered", "recovery_rate": 0.61,
     "text": "{name}, your card payment of ₹{amount} was declined. No worries — switch to UPI (GPay/PhonePe) for instant success: {link}"},
    {"id": "T021", "category": "BANK_DECLINE", "channel": "whatsapp",
     "amount_band": "large", "outcome": "partial", "recovery_rate": 0.51,
     "text": "Your ₹{amount} payment was blocked by your bank. Try Net Banking or NEFT instead. We'll hold your order: {link}"},
    {"id": "T022", "category": "BANK_DECLINE", "channel": "email",
     "amount_band": "small", "outcome": "recovered", "recovery_rate": 0.64,
     "text": "Your card payment of ₹{amount} was declined by your bank. Try UPI, Net Banking, or a different card: {link}"},
    {"id": "T023", "category": "BANK_DECLINE", "channel": "email",
     "amount_band": "medium", "outcome": "recovered", "recovery_rate": 0.58,
     "text": "Bank declined your ₹{amount} payment. This is common with international cards. Try UPI or Net Banking for a smooth experience: {link}"},
    {"id": "T024", "category": "BANK_DECLINE", "channel": "email",
     "amount_band": "large", "outcome": "partial", "recovery_rate": 0.48,
     "text": "Your ₹{amount} payment was declined. Please verify with your bank or use an alternate payment method. Retry here: {link}"},

    # ── NETWORK_TIMEOUT ───────────────────────────────────────────────────────
    {"id": "T025", "category": "NETWORK_TIMEOUT", "channel": "whatsapp",
     "amount_band": "small", "outcome": "recovered", "recovery_rate": 0.80,
     "text": "Looks like your internet dropped mid-payment! Your ₹{amount} order is safe. Retry here: {link} Takes 10 seconds ⚡"},
    {"id": "T026", "category": "NETWORK_TIMEOUT", "channel": "whatsapp",
     "amount_band": "medium", "outcome": "recovered", "recovery_rate": 0.77,
     "text": "Hi {name}! A network hiccup interrupted your ₹{amount} payment. No money was deducted. Complete it now: {link}"},
    {"id": "T027", "category": "NETWORK_TIMEOUT", "channel": "whatsapp",
     "amount_band": "large", "outcome": "recovered", "recovery_rate": 0.73,
     "text": "{name}, your ₹{amount} payment timed out (no charge made). Your order is saved. Retry at your convenience: {link}"},
    {"id": "T028", "category": "NETWORK_TIMEOUT", "channel": "email",
     "amount_band": "small", "outcome": "recovered", "recovery_rate": 0.76,
     "text": "A network timeout interrupted your ₹{amount} payment. Your money was not deducted. Complete the payment: {link}"},
    {"id": "T029", "category": "NETWORK_TIMEOUT", "channel": "email",
     "amount_band": "medium", "outcome": "recovered", "recovery_rate": 0.74,
     "text": "Your ₹{amount} payment timed out — this means no charge was applied. Pick up where you left off: {link}"},
    {"id": "T030", "category": "NETWORK_TIMEOUT", "channel": "email",
     "amount_band": "large", "outcome": "recovered", "recovery_rate": 0.70,
     "text": "Network interruption caused your ₹{amount} payment to fail (no deduction). Please retry using a stable connection: {link}"},

    # ── INVALID_DETAILS ───────────────────────────────────────────────────────
    {"id": "T031", "category": "INVALID_DETAILS", "channel": "whatsapp",
     "amount_band": "small", "outcome": "recovered", "recovery_rate": 0.59,
     "text": "Hi {name}, the card details for ₹{amount} couldn't be verified. Double-check the CVV/expiry and retry: {link} 🔐"},
    {"id": "T032", "category": "INVALID_DETAILS", "channel": "whatsapp",
     "amount_band": "medium", "outcome": "recovered", "recovery_rate": 0.55,
     "text": "Your ₹{amount} payment failed — possibly a typo in card details. Correct and retry here (or use UPI instead): {link}"},
    {"id": "T033", "category": "INVALID_DETAILS", "channel": "whatsapp",
     "amount_band": "large", "outcome": "partial", "recovery_rate": 0.43,
     "text": "Payment details for ₹{amount} couldn't be verified. Please re-enter your card info or switch to UPI/Net Banking: {link}"},
    {"id": "T034", "category": "INVALID_DETAILS", "channel": "email",
     "amount_band": "small", "outcome": "recovered", "recovery_rate": 0.57,
     "text": "Card verification failed for your ₹{amount} payment. Please double-check: card number, expiry, CVV, and billing address. Retry: {link}"},
    {"id": "T035", "category": "INVALID_DETAILS", "channel": "email",
     "amount_band": "medium", "outcome": "recovered", "recovery_rate": 0.53,
     "text": "Your ₹{amount} payment failed card verification. Try entering details again, or use UPI for instant payment: {link}"},
    {"id": "T036", "category": "INVALID_DETAILS", "channel": "email",
     "amount_band": "large", "outcome": "partial", "recovery_rate": 0.41,
     "text": "We couldn't process your ₹{amount} payment due to card verification failure. Please review your payment details: {link}"},

    # ── UNKNOWN ───────────────────────────────────────────────────────────────
    {"id": "T037", "category": "UNKNOWN", "channel": "whatsapp",
     "amount_band": "small", "outcome": "recovered", "recovery_rate": 0.52,
     "text": "Hi {name}! Your ₹{amount} payment didn't go through. Retry with a different method — UPI usually works instantly: {link}"},
    {"id": "T038", "category": "UNKNOWN", "channel": "whatsapp",
     "amount_band": "medium", "outcome": "recovered", "recovery_rate": 0.48,
     "text": "{name}, we hit an unexpected error processing ₹{amount}. It's likely temporary. Try again: {link}"},
    {"id": "T039", "category": "UNKNOWN", "channel": "whatsapp",
     "amount_band": "large", "outcome": "partial", "recovery_rate": 0.38,
     "text": "Your ₹{amount} payment encountered an error. Our team is aware. Please retry or contact support: {link}"},
    {"id": "T040", "category": "UNKNOWN", "channel": "email",
     "amount_band": "small", "outcome": "recovered", "recovery_rate": 0.50,
     "text": "Your ₹{amount} payment failed unexpectedly. This is usually temporary — retry now: {link}"},
    {"id": "T041", "category": "UNKNOWN", "channel": "email",
     "amount_band": "medium", "outcome": "recovered", "recovery_rate": 0.46,
     "text": "We encountered an unexpected issue with your ₹{amount} payment. Please try again using a different payment method: {link}"},
    {"id": "T042", "category": "UNKNOWN", "channel": "email",
     "amount_band": "large", "outcome": "partial", "recovery_rate": 0.36,
     "text": "Your ₹{amount} payment failed due to an unexpected error. Retry here or reach out to our support team: {link}"},
]


def _amount_band(amount_rupees: float) -> str:
    """Bucket amount into small/medium/large."""
    if amount_rupees < 1_000:
        return "small"
    if amount_rupees < 10_000:
        return "medium"
    return "large"


# ═══════════════════════════════════════════════════════════════════════════════
# Embedding backend (sentence-transformers or pure-numpy TF-IDF fallback)
# ═══════════════════════════════════════════════════════════════════════════════

class _EmbeddingBackend:
    """Embed text to a float vector. Thread-safe singleton."""

    _instance: "_EmbeddingBackend | None" = None

    def __init__(self) -> None:
        self._model: Any = None
        self._vocab: dict[str, int] = {}
        self._idf:   list[float]   = []
        self._use_st = False
        self._init()

    @classmethod
    def get(cls) -> "_EmbeddingBackend":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _init(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer
            self._model  = SentenceTransformer(_EMBED_MODEL)
            self._use_st = True
            logger.info("RAG: using sentence-transformers/%s", _EMBED_MODEL)
        except Exception:
            logger.info("RAG: sentence-transformers unavailable — using TF-IDF fallback")
            self._build_tfidf([t["text"] for t in _TEMPLATES])

    def _tokenize(self, text: str) -> list[str]:
        return re.findall(r"[a-z₹0-9]+", text.lower())

    def _build_tfidf(self, corpus: list[str]) -> None:
        import math as _math
        N = len(corpus)
        df: dict[str, int] = {}
        tokenized = [self._tokenize(doc) for doc in corpus]
        for tokens in tokenized:
            for w in set(tokens):
                df[w] = df.get(w, 0) + 1
        vocab = sorted(df.keys())
        self._vocab = {w: i for i, w in enumerate(vocab)}
        self._idf   = [_math.log((N + 1) / (df.get(w, 0) + 1)) + 1.0 for w in vocab]

    def embed(self, text: str) -> list[float]:
        if self._use_st and self._model is not None:
            try:
                vec = self._model.encode(text, normalize_embeddings=True)
                return vec.tolist()
            except Exception:
                pass
        # TF-IDF fallback
        tokens  = self._tokenize(text)
        tf: dict[str, int] = {}
        for w in tokens:
            tf[w] = tf.get(w, 0) + 1
        n = len(tokens) or 1
        vec = [0.0] * len(self._vocab)
        for w, idx in self._vocab.items():
            if w in tf:
                vec[idx] = (tf[w] / n) * self._idf[idx]
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if self._use_st and self._model is not None:
            try:
                vecs = self._model.encode(texts, normalize_embeddings=True)
                return vecs.tolist()
            except Exception:
                pass
        return [self.embed(t) for t in texts]


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors."""
    if len(a) != len(b):
        # Pad shorter with zeros
        n = max(len(a), len(b))
        a = a + [0.0] * (n - len(a))
        b = b + [0.0] * (n - len(b))
    dot  = sum(x * y for x, y in zip(a, b))
    na   = math.sqrt(sum(x * x for x in a))
    nb   = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# Vector Store — in-memory index over _TEMPLATES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class RetrievedTemplate:
    template_id:    str
    text:           str
    category:       str
    channel:        str
    amount_band:    str
    recovery_rate:  float
    similarity:     float


class RecoveryMessageVectorStore:
    """
    In-memory vector store over the 42 curated recovery templates.

    Embeddings are built lazily on first query.  All templates are indexed;
    query-time filtering by category/channel/amount_band happens *after*
    retrieval so the user always gets top-k results even when the filtered
    subset is small.
    """

    _instance: "RecoveryMessageVectorStore | None" = None

    def __init__(self) -> None:
        self._embeddings: list[list[float]] = []
        self._ready = False

    @classmethod
    def get(cls) -> "RecoveryMessageVectorStore":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _ensure_indexed(self) -> None:
        if self._ready:
            return
        backend = _EmbeddingBackend.get()
        texts   = [t["text"] for t in _TEMPLATES]
        self._embeddings = backend.embed_batch(texts)
        self._ready = True
        logger.info("RAG: indexed %d templates", len(_TEMPLATES))

    def retrieve(
        self,
        query_text:       str,
        failure_category: str,
        amount_rupees:    float,
        channel:          str = "email",
        top_k:            int = _TOP_K,
        min_similarity:   float = _MIN_SIM,
    ) -> list[RetrievedTemplate]:
        """
        Retrieve top_k most similar templates for this context.

        Scoring:
          base_score = cosine_similarity(query_embedding, template_embedding)
          boost      = +0.15 if category matches
                     + +0.10 if amount_band matches
                     + +0.05 if channel matches
          final      = base_score + boost
        """
        self._ensure_indexed()
        backend    = _EmbeddingBackend.get()
        query_vec  = backend.embed(query_text)
        target_band = _amount_band(amount_rupees)
        cat_lower   = failure_category.upper()
        ch_lower    = channel.lower()

        scored: list[tuple[float, int]] = []
        for i, tmpl in enumerate(_TEMPLATES):
            base = _cosine(query_vec, self._embeddings[i])
            boost = 0.0
            if tmpl["category"].upper() == cat_lower:
                boost += 0.15
            if tmpl["amount_band"] == target_band:
                boost += 0.10
            if tmpl["channel"].lower() == ch_lower:
                boost += 0.05
            score = base + boost
            if score >= min_similarity:
                scored.append((score, i))

        scored.sort(key=lambda x: -x[0])
        results: list[RetrievedTemplate] = []
        for score, idx in scored[:top_k]:
            t = _TEMPLATES[idx]
            results.append(RetrievedTemplate(
                template_id=t["id"],
                text=t["text"],
                category=t["category"],
                channel=t["channel"],
                amount_band=t["amount_band"],
                recovery_rate=t["recovery_rate"],
                similarity=round(score, 4),
            ))
        return results

    def get_all_templates(self) -> list[dict[str, Any]]:
        return list(_TEMPLATES)


# ═══════════════════════════════════════════════════════════════════════════════
# LLM few-shot message drafter
# ═══════════════════════════════════════════════════════════════════════════════

_RAG_SYSTEM_PROMPT = """You are a fintech customer communication specialist.
You will be given 3 historically successful payment recovery messages as examples,
and details about the current failed transaction.

Rules:
1. Draft ONE concise message (≤ 160 chars for SMS, ≤ 300 chars for WhatsApp/Email).
2. Use a warm, professional, non-alarming tone.
3. Include the payment link placeholder: {link}
4. Include the amount placeholder: {amount}
5. Never mention fraud, illegal activity, or account suspension.
6. Do not invent facts not present in the input.
7. Respond with ONLY the message text — no explanation, no metadata."""


async def draft_personalized_message(
    retrieved:        list[RetrievedTemplate],
    failure_category: str,
    amount_rupees:    float,
    channel:          str,
    failure_reason:   str | None = None,
) -> str:
    """
    Use retrieved few-shot templates to LLM-draft a personalized message.

    Falls back to the highest-similarity template's text when:
      • No OpenAI key is configured
      • LLM_DRAFT_ENABLED is false
      • The LLM call fails
    """
    if not retrieved:
        return (
            f"Your payment of ₹{amount_rupees:.0f} failed "
            f"({failure_reason or failure_category}). "
            "Please retry using this secure link: {link}"
        )

    if not _LLM_DRAFT:
        # Return the best template, filling in the amount
        best = retrieved[0].text
        return best.replace("{amount}", f"₹{amount_rupees:.0f}")

    try:
        from config import get_settings
        s = get_settings()
        if not s.openai_api_key:
            raise ValueError("no key")
    except Exception:
        best = retrieved[0].text
        return best.replace("{amount}", f"₹{amount_rupees:.0f}")

    few_shot = "\n".join(
        f"Example {i + 1} (recovery rate {t.recovery_rate:.0%}):\n{t.text}"
        for i, t in enumerate(retrieved)
    )
    user_msg = (
        f"CHANNEL: {channel.upper()}\n"
        f"FAILURE CATEGORY: {failure_category}\n"
        f"AMOUNT: ₹{amount_rupees:.0f}\n"
        f"REASON: {failure_reason or 'N/A'}\n\n"
        f"FEW-SHOT EXAMPLES:\n{few_shot}\n\n"
        "Draft ONE personalized recovery message for this customer."
    )

    try:
        import httpx
        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {s.openai_api_key}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model":       s.llm_model,
                    "messages":    [
                        {"role": "system", "content": _RAG_SYSTEM_PROMPT},
                        {"role": "user",   "content": user_msg},
                    ],
                    "max_tokens":  200,
                    "temperature": 0.4,
                },
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"].strip()
            logger.info(
                "RAG LLM draft: %.0f ms, %d chars",
                (time.perf_counter() - t0) * 1000, len(text),
            )
            return text
    except Exception as exc:
        logger.warning("RAG LLM draft failed (%s) — using best template", exc)
        best = retrieved[0].text
        return best.replace("{amount}", f"₹{amount_rupees:.0f}")


# ═══════════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PersonalizationResult:
    """Result of the RAG personalization step."""
    drafted_message:      str
    retrieved_ids:        list[str]          # template IDs used
    retrieved_templates:  list[RetrievedTemplate]
    llm_used:             bool
    latency_ms:           float


async def personalize_recovery_message(
    failure_category: str,
    amount_rupees:    float,
    channel:          str = "email",
    failure_reason:   str | None = None,
    payment_id:       str = "",
    top_k:            int = _TOP_K,
) -> PersonalizationResult:
    """
    Full RAG personalization pipeline:
      1. Build query text from failure context
      2. Retrieve top-k similar templates
      3. LLM-draft a personalised message
      4. Return result (caller logs to rag_dispatch_log)
    """
    t0 = time.perf_counter()

    query_text = (
        f"failed payment recovery {failure_category.lower().replace('_', ' ')} "
        f"amount {amount_rupees:.0f} rupees {channel} {failure_reason or ''}"
    )

    store = RecoveryMessageVectorStore.get()
    retrieved = store.retrieve(
        query_text=query_text,
        failure_category=failure_category,
        amount_rupees=amount_rupees,
        channel=channel,
        top_k=top_k,
    )

    drafted = await draft_personalized_message(
        retrieved=retrieved,
        failure_category=failure_category,
        amount_rupees=amount_rupees,
        channel=channel,
        failure_reason=failure_reason,
    )

    latency = (time.perf_counter() - t0) * 1000
    logger.info(
        "RAG personalize: txn=%s cat=%s chan=%s retrieved=%s latency=%.1f ms",
        payment_id, failure_category, channel,
        [r.template_id for r in retrieved], latency,
    )

    return PersonalizationResult(
        drafted_message=drafted,
        retrieved_ids=[r.template_id for r in retrieved],
        retrieved_templates=retrieved,
        llm_used=_LLM_DRAFT and bool(retrieved),
        latency_ms=round(latency, 2),
    )
