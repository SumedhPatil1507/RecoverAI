"""Revenue and payment-failure anomaly detection with alert integrations."""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from typing import Any

import numpy as np
import pandas as pd

try:
    from sklearn.ensemble import IsolationForest
except ImportError:  # pragma: no cover
    IsolationForest = None

try:
    from prophet import Prophet
except ImportError:  # optional production dependency
    Prophet = None


def build_features(transactions: list[dict[str, Any]]) -> pd.DataFrame:
    if not transactions:
        return pd.DataFrame(columns=["timestamp", "failures", "amount_paise"])
    df = pd.DataFrame(transactions)
    df["timestamp"] = pd.to_datetime(df.get("created_at"), errors="coerce", utc=True)
    df["amount_paise"] = pd.to_numeric(df.get("amount_paise"), errors="coerce").fillna(0)
    grouped = df.dropna(subset=["timestamp"]).set_index("timestamp").resample("5min").agg(failures=("payment_id", "count"), amount_paise=("amount_paise", "sum")).reset_index()
    return grouped.fillna(0)


def detect(transactions: list[dict[str, Any]], z_threshold: float = 3.0) -> dict[str, Any]:
    features = build_features(transactions)
    if features.empty:
        return {"anomalies": [], "merchant_impact": {}, "method": "Isolation Forest + statistical thresholding"}
    values = features[["failures", "amount_paise"]].astype(float)
    if len(values) >= 8 and IsolationForest is not None:
        model = IsolationForest(contamination="auto", random_state=42)
        labels = model.fit_predict(values)
        features["isolation_anomaly"] = labels == -1
    else:
        features["isolation_anomaly"] = False
    rolling = values["failures"].rolling(6, min_periods=2)
    mean = rolling.mean()
    std = rolling.std().replace(0, np.nan).fillna(1)
    features["failure_zscore"] = (values["failures"] - mean) / std
    features["threshold_anomaly"] = features["failure_zscore"] >= z_threshold
    features["is_anomaly"] = features["isolation_anomaly"] | features["threshold_anomaly"]
    anomalies = features[features["is_anomaly"]].to_dict("records")
    impact: dict[str, float] = {}
    root_causes: dict[str, int] = {}
    for txn in transactions:
        merchant = str(txn.get("merchant_id") or "default")
        category = str(txn.get("failure_category") or txn.get("failure_code") or "UNKNOWN")
        impact[merchant] = impact.get(merchant, 0.0) + float(txn.get("amount_paise") or 0) / 100
        root_causes[category] = root_causes.get(category, 0) + 1
    predicted_root_cause = max(root_causes, key=root_causes.get) if root_causes else "UNKNOWN"
    return {"anomalies": anomalies, "merchant_impact": impact, "root_cause_prediction": predicted_root_cause, "method": "Isolation Forest + statistical thresholding", "forecast_method": "Prophet when installed; rolling baseline otherwise"}


def forecast(transactions: list[dict[str, Any]], periods: int = 12) -> pd.DataFrame:
    features = build_features(transactions)
    if features.empty:
        return pd.DataFrame(columns=["timestamp", "predicted_failures"])
    if Prophet is not None and len(features) >= 10:
        model = Prophet(daily_seasonality=False, weekly_seasonality=False)
        train = features.rename(columns={"timestamp": "ds", "failures": "y"})[["ds", "y"]]
        model.fit(train)
        future = model.make_future_dataframe(periods=periods, freq="5min")
        predicted = model.predict(future)[["ds", "yhat"]].tail(periods).rename(columns={"ds": "timestamp", "yhat": "predicted_failures"})
        return predicted
    baseline = float(features["failures"].tail(6).mean())
    timestamps = pd.date_range(features["timestamp"].max() + pd.Timedelta(minutes=5), periods=periods, freq="5min", tz="UTC")
    return pd.DataFrame({"timestamp": timestamps, "predicted_failures": [baseline] * periods})


def send_slack_alert(message: str) -> bool:
    import requests
    url = os.getenv("SLACK_WEBHOOK_URL", "")
    if not url:
        return False
    response = requests.post(url, json={"text": message}, timeout=10)
    return response.ok


def send_email_alert(subject: str, body: str) -> bool:
    host = os.getenv("SMTP_HOST", "")
    recipient = os.getenv("ALERT_EMAIL_TO", "")
    sender = os.getenv("ALERT_EMAIL_FROM", recipient)
    if not host or not recipient:
        return False
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, sender, recipient
    msg.set_content(body)
    with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=10) as server:
        if os.getenv("SMTP_TLS", "true").lower() == "true":
            server.starttls()
        if os.getenv("SMTP_USER"):
            server.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD", ""))
        server.send_message(msg)
    return True
