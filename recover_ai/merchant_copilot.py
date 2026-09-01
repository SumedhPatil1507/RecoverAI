"""Merchant Copilot: safe RAG + natural-language analytics over RecoverAI data."""
from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
from collections.abc import Generator, Iterable
from typing import Any

import database as db

try:
    from langgraph.graph import END, START, StateGraph
except ImportError:  # pragma: no cover - deployment fallback
    StateGraph = None
    START = END = None

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - optional until configured
    OpenAI = None

ALLOWED_TABLES = {"transactions", "audit_logs"}
ALLOWED_COLUMNS = {
    "transactions": {
        "payment_id", "order_id", "amount_paise", "currency", "status",
        "failure_code", "failure_reason", "failure_category",
        "recoverability_score", "recovery_attempts", "merchant_id", "created_at", "updated_at",
    },
    "audit_logs": {
        "log_id", "transaction_id", "action_taken", "decision_rationale", "source",
        "recoverability_score", "timestamp", "merchant_id",
    },
}
BLOCKED_SQL = re.compile(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|ATTACH|DETACH|PRAGMA|VACUUM|REPLACE|CREATE|GRANT|REVOKE)\b", re.I)


def _rows() -> list[dict[str, Any]]:
    txns = [dict(r) for r in db.get_all_transactions(500)]
    logs = [dict(r) for r in db.get_audit_logs(500)]
    return txns + logs


def retrieve_context(question: str, limit: int = 12) -> list[dict[str, Any]]:
    """Lightweight local RAG retriever that works on SQLite and Postgres adapters."""
    terms = {t.lower() for t in re.findall(r"[a-zA-Z0-9_₹]+", question) if len(t) > 2}
    scored: list[tuple[int, dict[str, Any]]] = []
    for row in _rows():
        text = json.dumps(row, default=str).lower()
        score = sum(1 for term in terms if term in text)
        if score or not terms:
            scored.append((score, row))
    scored.sort(key=lambda item: (item[0], str(item[1].get("created_at", item[1].get("timestamp", "")))), reverse=True)
    return [row for _, row in scored[:limit]]


def validate_sql(sql: str) -> tuple[bool, str]:
    """Allow only one read-only aggregate/query statement over approved columns."""
    candidate = sql.strip().strip(";").strip()
    if not candidate:
        return False, "SQL is empty."
    if ";" in candidate:
        return False, "Multiple SQL statements are not allowed."
    if not re.match(r"^SELECT\b", candidate, re.I):
        return False, "Only SELECT statements are allowed."
    if BLOCKED_SQL.search(candidate):
        return False, "Write, schema, and administrative SQL keywords are blocked."
    tables = set(re.findall(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", candidate, re.I))
    if not tables or not tables.issubset(ALLOWED_TABLES):
        return False, f"Only these tables are available: {', '.join(sorted(ALLOWED_TABLES))}."
    if " limit " not in f" {candidate.lower()} " and not candidate.lower().endswith(" limit"):
        candidate += " LIMIT 200"
    if re.search(r"\bLIMIT\s+(\d+)", candidate, re.I) and int(re.search(r"\bLIMIT\s+(\d+)", candidate, re.I).group(1)) > 500:
        return False, "LIMIT cannot exceed 500."
    return True, candidate


def execute_validated_sql(sql: str) -> list[dict[str, Any]]:
    ok, validated = validate_sql(sql)
    if not ok:
        raise ValueError(validated)
    with db.get_db() as conn:
        rows = conn.execute(validated).fetchall()
    return [dict(row) for row in rows]


def _fallback_sql(question: str) -> str:
    q = question.lower()
    if "root" in q or "cause" in q or "category" in q:
        return "SELECT failure_category, COUNT(*) AS failures, SUM(amount_paise) AS amount_paise FROM transactions WHERE failure_category IS NOT NULL GROUP BY failure_category ORDER BY failures DESC LIMIT 50"
    if "recovered" in q or "recovery rate" in q:
        return "SELECT status, COUNT(*) AS transactions, SUM(amount_paise) AS amount_paise FROM transactions GROUP BY status ORDER BY transactions DESC LIMIT 50"
    if "audit" in q or "action" in q:
        return "SELECT action_taken, source, COUNT(*) AS actions FROM audit_logs GROUP BY action_taken, source ORDER BY actions DESC LIMIT 50"
    return "SELECT status, failure_category, COUNT(*) AS transactions, SUM(amount_paise) AS amount_paise FROM transactions GROUP BY status, failure_category ORDER BY transactions DESC LIMIT 50"


def generate_sql(question: str, context: list[dict[str, Any]] | None = None) -> str:
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key or OpenAI is None:
        return _fallback_sql(question)
    schema = "transactions(" + ",".join(sorted(ALLOWED_COLUMNS["transactions"])) + ")\naudit_logs(" + ",".join(sorted(ALLOWED_COLUMNS["audit_logs"])) + ")"
    prompt = (
        "Generate exactly one read-only SQLite SELECT query. No markdown, no comments, no semicolon. "
        "Use only the schema below, aggregate when useful, and include LIMIT 200 or less. "
        f"Schema: {schema}\nQuestion: {question}\nContext: {json.dumps(context or [], default=str)[:5000]}"
    )
    try:
        client = OpenAI(api_key=api_key, base_url=os.getenv("OPENAI_API_BASE") or None)
        response = client.chat.completions.create(model=os.getenv("COPILOT_MODEL", "gpt-4o-mini"), messages=[{"role": "system", "content": "You are a safe SQL analyst."}, {"role": "user", "content": prompt}], temperature=0, max_tokens=400)
        sql = response.choices[0].message.content or ""
        return re.sub(r"```(?:sql)?|```", "", sql, flags=re.I).strip()
    except Exception:
        return _fallback_sql(question)


def explain_metrics(rows: Iterable[dict[str, Any]]) -> str:
    rows = list(rows)
    if not rows:
        return "No matching transaction or audit records were found."
    amount = sum(float(r.get("amount_paise") or r.get("amount_paise", 0) or 0) for r in rows) / 100
    recovered = sum(1 for r in rows if r.get("status") == "RECOVERED")
    return f"The result contains {len(rows):,} records and ₹{amount:,.2f} in represented transaction value. {recovered:,} records are marked RECOVERED; percentages are calculated from the returned rows only."


def _answer_direct(question: str) -> dict[str, Any]:
    context = retrieve_context(question)
    sql = generate_sql(question, context)
    try:
        rows = execute_validated_sql(sql)
        error = None
    except ValueError as exc:
        rows, error = [], str(exc)
    text = explain_metrics(rows)
    if error:
        text = f"I could not run the generated query safely: {error}"
    return {"answer": text, "sql": sql, "rows": rows, "context": context, "error": error}


def stream_text(text: str, chunk_size: int = 24) -> Generator[str, None, None]:
    for start in range(0, len(text), chunk_size):
        yield text[start:start + chunk_size]


def suggested_questions() -> list[str]:
    return [
        "What are the top failure root causes by revenue at risk?",
        "Which recovery actions are associated with the highest recovered revenue?",
        "Explain the current recovery rate and its main drivers.",
        "Show recent audit actions and any transactions still recovering.",
    ]


def build_langgraph():
    """Build a small inspectable LangGraph for Copilot requests when installed."""
    if StateGraph is None:
        return None
    graph = StateGraph(dict)
    graph.add_node("retrieve", lambda state: {"context": retrieve_context(state["question"])})
    graph.add_node("query", lambda state: {"sql": generate_sql(state["question"], state.get("context", []))})
    graph.add_node("validate_execute", lambda state: {"result": _answer_direct(state["question"])})
    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "query")
    graph.add_edge("query", "validate_execute")
    graph.add_edge("validate_execute", END)
    return graph.compile()


_GRAPH = None


def answer(question: str) -> dict[str, Any]:
    """Run the Copilot graph, falling back to the direct safe pipeline."""
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_langgraph()
    if _GRAPH is not None:
        try:
            state = _GRAPH.invoke({"question": question})
            return state.get("result", _answer_direct(question))
        except Exception:
            pass
    return _answer_direct(question)
