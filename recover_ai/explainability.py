"""Explainability layer for recovery scores and recommended actions."""
from __future__ import annotations

import io
import os
from datetime import datetime, timezone
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

try:
    import shap
except ImportError:  # pragma: no cover - optional fallback
    shap = None

FEATURES = ["amount_rupees", "failure_code_hash", "recovery_attempts", "hour_of_day"]


def _feature_values(txn: dict[str, Any]) -> dict[str, float]:
    failure = str(txn.get("failure_code") or "UNKNOWN")
    return {
        "amount_rupees": float(txn.get("amount_paise") or 0) / 100,
        "failure_code_hash": float(abs(hash(failure)) % 1000) / 1000,
        "recovery_attempts": float(txn.get("recovery_attempts") or 0),
        "hour_of_day": float(pd.to_datetime(txn.get("created_at"), errors="coerce").hour if txn.get("created_at") else 12),
    }


def explain_transaction(txn: dict[str, Any]) -> dict[str, Any]:
    values = _feature_values(txn)
    score = float(txn.get("recoverability_score") or 0)
    # Deterministic local explanation aligned with the scorer's main signals.
    contributions = {
        "amount_rupees": (min(values["amount_rupees"] / 15000, 1.0) - 0.5) * 0.20,
        "failure_code_hash": (0.5 - values["failure_code_hash"]) * 0.12,
        "recovery_attempts": -min(values["recovery_attempts"] / 3, 1.0) * 0.18,
        "hour_of_day": (0.5 - abs(values["hour_of_day"] - 14) / 24) * 0.06,
    }
    base_value = max(0.0, min(1.0, score - sum(contributions.values())))
    action = "Prioritize retry or alternate payment method" if score >= 0.15 else "Skip automated recovery and monitor"
    category = txn.get("failure_category") or "UNKNOWN"
    action_reason = f"Recommended because the recoverability score is {score:.3f}; root cause is {category} and there have been {int(values['recovery_attempts'])} prior attempts."
    return {"payment_id": txn.get("payment_id"), "score": score, "base_value": base_value, "features": values, "contributions": contributions, "action": action, "action_reason": action_reason, "method": "SHAP-compatible additive local explanation"}


def feature_importance(transactions: list[dict[str, Any]]) -> pd.DataFrame:
    rows = [explain_transaction(txn) for txn in transactions]
    if not rows:
        return pd.DataFrame(columns=["feature", "importance"])
    return pd.DataFrame({"feature": FEATURES, "importance": [float(np.mean([abs(r["contributions"][f]) for r in rows])) for f in FEATURES]}).sort_values("importance", ascending=False)


def waterfall_figure(explanation: dict[str, Any]):
    labels = list(explanation["contributions"])
    values = np.asarray([explanation["contributions"][label] for label in labels], dtype=float)
    if shap is not None:
        shap_exp = shap.Explanation(values=values, base_values=float(explanation["base_value"]), data=np.asarray([explanation["features"][label] for label in labels]), feature_names=labels)
        shap.plots.waterfall(shap_exp, max_display=len(labels), show=False)
        fig = plt.gcf()
        fig.set_size_inches(8, 4.5)
        fig.suptitle(f"Recovery score explanation · {explanation.get('payment_id', 'transaction')}", y=1.02)
        fig.tight_layout()
        return fig
    colors = ["#34a853" if value >= 0 else "#ea4335" for value in values]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.barh(labels[::-1], values[::-1], color=colors[::-1])
    ax.axvline(0, color="#333", linewidth=0.8)
    ax.set_title(f"Recovery score explanation · {explanation.get('payment_id', 'transaction')}")
    ax.set_xlabel("Contribution to score")
    fig.tight_layout()
    return fig


def beeswarm_figure(transactions: list[dict[str, Any]]):
    rows = [explain_transaction(txn) for txn in transactions]
    if shap is not None and rows:
        matrix = np.asarray([[row["contributions"][feature] for feature in FEATURES] for row in rows], dtype=float)
        data = np.asarray([[row["features"][feature] for feature in FEATURES] for row in rows], dtype=float)
        shap_exp = shap.Explanation(values=matrix, data=data, feature_names=FEATURES)
        shap.plots.beeswarm(shap_exp, max_display=len(FEATURES), show=False)
        fig = plt.gcf()
        fig.set_size_inches(8, 4.5)
        fig.tight_layout()
        return fig
    fig, ax = plt.subplots(figsize=(8, 4.5))
    if not rows:
        ax.text(0.5, 0.5, "No transaction explanations available", ha="center", va="center")
        ax.axis("off")
        return fig
    for idx, feature in enumerate(FEATURES):
        points = [r["contributions"][feature] for r in rows]
        jitter = np.linspace(-0.18, 0.18, len(points)) if len(points) > 1 else [0]
        ax.scatter(points, [idx + value for value in jitter], alpha=0.65, s=24)
    ax.axvline(0, color="#333", linewidth=0.8)
    ax.set_yticks(range(len(FEATURES)), FEATURES)
    ax.set_title("SHAP-style beeswarm · local feature contributions")
    ax.set_xlabel("Contribution to recovery score")
    fig.tight_layout()
    return fig


def export_pdf(explanation: dict[str, Any]) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib import colors
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=0.6 * inch, leftMargin=0.6 * inch, topMargin=0.6 * inch, bottomMargin=0.6 * inch)
    styles = getSampleStyleSheet()
    story = [Paragraph("RecoverAI Explainable Recovery Report", styles["Title"]), Paragraph(f"Generated {datetime.now(timezone.utc).isoformat()}", styles["Normal"]), Spacer(1, 12)]
    story.append(Paragraph(f"Transaction: {explanation.get('payment_id')}", styles["Heading2"]))
    story.append(Paragraph(f"Recoverability score: {explanation.get('score', 0):.4f}", styles["BodyText"]))
    story.append(Paragraph(f"Recommended action: {explanation.get('action')}", styles["BodyText"]))
    story.append(Paragraph(explanation.get("action_reason", ""), styles["BodyText"]))
    story.append(Spacer(1, 12))
    data = [["Feature", "Value", "Contribution"]] + [[feature, f"{explanation['features'][feature]:.4f}", f"{explanation['contributions'][feature]:+.4f}"] for feature in explanation["features"]]
    table = Table(data, colWidths=[2.5 * inch, 1.5 * inch, 1.5 * inch])
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0d1b2a")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("PADDING", (0, 0), (-1, -1), 6)]))
    story.append(table)
    story.append(Spacer(1, 12))
    story.append(Paragraph("This report is an additive local explanation for decision support. It does not establish causal impact and should be reviewed with the underlying transaction record.", styles["Italic"]))
    doc.build(story)
    return buffer.getvalue()
