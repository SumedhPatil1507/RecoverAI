import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "recover_ai"))

from fastapi.testclient import TestClient
import merchant_copilot
import explainability
import recovery_optimization
import experimentation
import anomaly_detection
from main import app


def test_ai_modules_end_to_end():
    assert merchant_copilot.validate_sql("DELETE FROM transactions")[0] is False
    result = merchant_copilot.answer("What are the top failure root causes?")
    assert "sql" in result and "rows" in result
    txn = {"payment_id": "pay_test", "amount_paise": 100000, "failure_code": "GATEWAY_ERROR", "failure_category": "GATEWAY_DOWN", "recoverability_score": 0.7, "recovery_attempts": 0, "created_at": "2026-08-29T12:00:00+00:00"}
    explanation = explainability.explain_transaction(txn)
    assert len(explanation["contributions"]) == 4
    assert explainability.export_pdf(explanation).startswith(b"%PDF")
    rec = recovery_optimization.recommend(txn)
    assert 0 < rec["expected_success_probability"] <= 1
    experimentation.record("retry_now", True, 1000, 1, 10)
    assert experimentation.report()["strategies"][0]["recovered"] == 1
    anomaly = anomaly_detection.detect([txn])
    assert "anomalies" in anomaly
    client = TestClient(app)
    assert client.post("/api/copilot/query", json={"question": "show failure categories"}).status_code == 200
    assert client.post("/api/recovery/optimize", json=txn).status_code == 200
    assert client.post("/api/anomalies/scan", json={"transactions": [txn]}).status_code == 200



def main():
    test_ai_modules_end_to_end()
    print("AI module integration smoke test passed")


if __name__ == "__main__":
    main()
