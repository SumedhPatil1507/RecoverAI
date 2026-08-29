"""
RecoverAI Enterprise – Dual-Mode Data Simulator + Chaos Stress Tester
======================================================================
Modes:
  default          Synthetic HMAC-signed payment.failed events (interval loop)
  --burst N        Send N events as fast as possible (sequential)
  --chaos N        Concurrent chaos stress test: N worker threads sending
                   HMAC-signed webhooks simultaneously, measuring ACK latency,
                   dropped requests, and p95/p99 response times
  --live           Single live-integration event (Razorpay test mode format)

Usage:
  python data_simulator.py                   # continuous synthetic mode
  python data_simulator.py --burst 50        # 50 fast sequential events
  python data_simulator.py --chaos 500       # 500 concurrent HMAC webhooks
  python data_simulator.py --count 20        # exactly 20 events then stop
  python data_simulator.py --live            # single live test event
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import math
import random
import statistics
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any

import httpx

sys.path.insert(0, __file__.replace("data_simulator.py", ""))

from config import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)
logger = logging.getLogger("simulator")
settings = get_settings()


# ── Scenario library ──────────────────────────────────────────────────────────

SCENARIOS: list[dict] = [
    {"error_code": "GATEWAY_ERROR",      "error_description": "Acquiring bank gateway returned 500",             "error_reason": "bank_downtime",   "weight": 18},
    {"error_code": "NETWORK_TIMEOUT",    "error_description": "Connection timed out during authorization",       "error_reason": "network_timeout",  "weight": 15},
    {"error_code": "PAYMENT_CANCELLED",  "error_description": "User closed checkout window before completing",   "error_reason": "user_cancelled",   "weight": 25},
    {"error_code": "BAD_REQUEST_ERROR",  "error_description": "Payment abandoned by customer mid-flow",          "error_reason": "user_cancelled",   "weight": 15},
    {"error_code": "INSUFFICIENT_FUNDS", "error_description": "Insufficient account balance",                    "error_reason": "low_balance",      "weight": 14},
    {"error_code": "CARD_DECLINED",      "error_description": "Card declined by issuing bank",                   "error_reason": "bank_decline",     "weight": 8},
    {"error_code": "INVALID_CARD",       "error_description": "Invalid card number or expired details",          "error_reason": "invalid_details",  "weight": 5},
]

METHODS   = ["upi", "card", "netbanking", "wallet", "emi"]
MERCHANTS = ["merchant_razorpay_001", "merchant_razorpay_002", "merchant_razorpay_003"]
EMAILS    = ["customer1@example.com", "user2@gmail.com", "buyer3@yahoo.com", "shopper4@hotmail.com"]
_WEIGHTS  = [s["weight"] for s in SCENARIOS]


def _build_synthetic_event() -> dict:
    scenario     = random.choices(SCENARIOS, weights=_WEIGHTS, k=1)[0]
    payment_id   = f"pay_{uuid.uuid4().hex[:16]}"
    order_id     = f"order_{uuid.uuid4().hex[:16]}"
    amount_paise = random.randint(
        settings.min_transaction_amount_paise,
        settings.max_transaction_amount_paise,
    )
    return {
        "entity": "event",
        "account_id": random.choice(MERCHANTS),
        "event": "payment.failed",
        "contains": ["payment"],
        "payload": {"payment": {"entity": {
            "id": payment_id,
            "entity": "payment",
            "order_id": order_id,
            "amount": amount_paise,
            "currency": "INR",
            "status": "failed",
            "method": random.choice(METHODS),
            "email": random.choice(EMAILS),
            "contact": f"+91{random.randint(7000000000, 9999999999)}",
            "error_code": scenario["error_code"],
            "error_description": scenario["error_description"],
            "error_reason": scenario["error_reason"],
            "error_source": "customer",
            "created_at": int(datetime.now(timezone.utc).timestamp()),
        }}},
    }


def _build_live_event(payment_id: str, order_id: str, amount_paise: int) -> dict:
    return {
        "entity": "event",
        "event": "payment.failed",
        "contains": ["payment"],
        "payload": {"payment": {"entity": {
            "id": payment_id,
            "order_id": order_id,
            "amount": amount_paise,
            "currency": "INR",
            "status": "failed",
            "method": "card",
            "email": "live_test@razorpay.com",
            "error_code": "GATEWAY_ERROR",
            "error_description": "Live test event from Razorpay test mode",
            "error_reason": "bank_downtime",
            "error_source": "gateway",
            "created_at": int(datetime.now(timezone.utc).timestamp()),
        }}},
    }


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _send(client: httpx.Client, payload: dict, idx: int) -> tuple[bool, float]:
    """Send one event. Returns (success, latency_ms)."""
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sig  = _sign(body, settings.razorpay_webhook_secret)
    url  = f"{settings.webhook_base_url}/webhook/razorpay"
    t0   = time.perf_counter()
    try:
        resp = client.post(
            url,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Razorpay-Signature": sig,
                "User-Agent": "RecoverAI-Simulator/2.0",
            },
            timeout=10.0,
        )
        latency_ms = (time.perf_counter() - t0) * 1000
        ok = resp.status_code == 200
        pid  = payload["payload"]["payment"]["entity"]["id"]
        amt  = payload["payload"]["payment"]["entity"]["amount"] / 100
        code = payload["payload"]["payment"]["entity"]["error_code"]
        logger.info(
            "[%05d] %-22s ₹%8.2f  HTTP %d  %.1fms  (%s)",
            idx, pid, amt, resp.status_code, latency_ms, code,
        )
        return ok, latency_ms
    except httpx.ConnectError:
        logger.error("Cannot connect to %s — is the API running?", url)
        return False, 0.0
    except httpx.TimeoutException:
        logger.warning("Request timeout on event %d", idx)
        return False, 10_000.0
    except Exception as exc:
        logger.error("Error on event %d: %s", idx, exc)
        return False, 0.0


# ── Chaos stress-test ─────────────────────────────────────────────────────────

@dataclass
class ChaosResult:
    total:        int   = 0
    success:      int   = 0
    failed:        int  = 0
    dropped:      int   = 0
    latencies_ms: list[float] = field(default_factory=list)
    wall_time_s:  float = 0.0
    _lock:        Lock  = field(default_factory=Lock, repr=False, compare=False)

    def record(self, ok: bool, latency_ms: float) -> None:
        with self._lock:
            self.total += 1
            if ok:
                self.success += 1
                self.latencies_ms.append(latency_ms)
            else:
                self.failed += 1
                if latency_ms == 0.0:
                    self.dropped += 1

    def percentile(self, pct: float) -> float:
        if not self.latencies_ms:
            return 0.0
        sorted_lat = sorted(self.latencies_ms)
        idx = math.ceil(pct / 100 * len(sorted_lat)) - 1
        return sorted_lat[max(0, idx)]

    def print_report(self) -> None:
        lat = self.latencies_ms
        print("\n" + "=" * 65)
        print("  RecoverAI Chaos Stress Test — Results")
        print("=" * 65)
        print(f"  Total requests  : {self.total}")
        print(f"  Successful ACKs : {self.success}  ({self.success/max(1,self.total)*100:.1f}%)")
        print(f"  Failed / errors : {self.failed}")
        print(f"  Dropped (0-lat) : {self.dropped}")
        print(f"  Wall time       : {self.wall_time_s:.2f} s")
        print(f"  Throughput      : {self.total/max(0.001,self.wall_time_s):.1f} req/s")
        if lat:
            print(f"\n  Latency (ms) — successful ACKs only:")
            print(f"    min  : {min(lat):.1f}")
            print(f"    mean : {statistics.mean(lat):.1f}")
            print(f"    p50  : {self.percentile(50):.1f}")
            print(f"    p95  : {self.percentile(95):.1f}")
            print(f"    p99  : {self.percentile(99):.1f}")
            print(f"    max  : {max(lat):.1f}")
            sla_ok = self.percentile(95) < 30
            print(f"\n  SLA <30ms p95   : {'✅ PASS' if sla_ok else '❌ FAIL'}")
        print("=" * 65 + "\n")


def _worker_send(args: tuple[int, str, str]) -> tuple[bool, float]:
    """Thread worker: creates its own httpx client to avoid sharing connections."""
    idx, url_base, secret = args
    payload = _build_synthetic_event()
    body    = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sig     = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    url     = f"{url_base}/webhook/razorpay"
    t0      = time.perf_counter()
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Razorpay-Signature": sig,
                    "User-Agent": "RecoverAI-ChaosTest/1.0",
                },
            )
        latency_ms = (time.perf_counter() - t0) * 1000
        return resp.status_code == 200, latency_ms
    except Exception as exc:
        latency_ms = (time.perf_counter() - t0) * 1000
        logger.debug("Worker %d error: %s", idx, exc)
        return False, latency_ms


def _run_chaos(n: int = 500, max_workers: int = 64) -> ChaosResult:
    """
    Spawn up to `max_workers` concurrent threads, each sending one HMAC-signed
    webhook.  Measures ACK latency, verifies sub-30ms p95 SLA, and reports
    dropped requests.
    """
    print("\n" + "=" * 65)
    print(f"  RecoverAI Chaos Stress Test")
    print(f"  Target  : {settings.webhook_base_url}")
    print(f"  Events  : {n}")
    print(f"  Workers : {max_workers} concurrent threads")
    print("=" * 65)

    result  = ChaosResult()
    secret  = settings.razorpay_webhook_secret
    url_base = settings.webhook_base_url

    # Verify server is reachable before starting
    try:
        with httpx.Client(timeout=5.0) as probe:
            health = probe.get(f"{url_base}/health")
            if health.status_code != 200:
                logger.error("Server health check failed (HTTP %d). Aborting chaos test.", health.status_code)
                return result
        logger.info("Server reachable — starting chaos test…")
    except Exception as exc:
        logger.error("Cannot reach server at %s: %s\nStart FastAPI first: uvicorn recover_ai.main:app", url_base, exc)
        return result

    wall_start = time.perf_counter()
    tasks = [(i, url_base, secret) for i in range(1, n + 1)]

    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_worker_send, t): t for t in tasks}
        for future in as_completed(futures):
            try:
                ok, latency_ms = future.result()
            except Exception:
                ok, latency_ms = False, 0.0
            result.record(ok, latency_ms)
            completed += 1
            if completed % 50 == 0:
                print(f"  Progress: {completed}/{n} — "
                      f"ok={result.success} fail={result.failed} "
                      f"p95={result.percentile(95):.1f}ms")

    result.wall_time_s = time.perf_counter() - wall_start
    result.print_report()
    return result


# ── Sequential modes ──────────────────────────────────────────────────────────

def _run_synthetic(count: int | None, interval: float) -> None:
    logger.info("=" * 65)
    logger.info("RecoverAI Data Simulator v2.0  [SYNTHETIC MODE]")
    logger.info("Target   : %s", settings.webhook_base_url)
    logger.info("Interval : %.1f s   Count: %s", interval, count or "∞")
    logger.info("Ctrl-C to stop")
    logger.info("=" * 65)

    with httpx.Client() as client:
        for i in range(1, 9):
            _send(client, _build_synthetic_event(), i)
            time.sleep(0.25)

        idx, sent = 9, 8
        while count is None or sent < count:
            time.sleep(interval)
            ok, _ = _send(client, _build_synthetic_event(), idx)
            if ok:
                sent += 1
            idx += 1
            if count and sent >= count:
                break

    logger.info("Simulation complete. %d events sent.", sent)


def _run_burst(n: int) -> None:
    logger.info("BURST MODE: sending %d events as fast as possible…", n)
    latencies: list[float] = []
    with httpx.Client() as client:
        for i in range(1, n + 1):
            ok, lat = _send(client, _build_synthetic_event(), i)
            if ok:
                latencies.append(lat)

    if latencies:
        logger.info(
            "Burst complete. mean=%.1fms p95=%.1fms p99=%.1fms",
            statistics.mean(latencies),
            sorted(latencies)[math.ceil(0.95 * len(latencies)) - 1],
            sorted(latencies)[math.ceil(0.99 * len(latencies)) - 1],
        )


def _run_live() -> None:
    logger.info("LIVE INTEGRATION MODE")
    payload = _build_live_event(
        payment_id=f"pay_live_{uuid.uuid4().hex[:12]}",
        order_id=f"order_live_{uuid.uuid4().hex[:12]}",
        amount_paise=250000,
    )
    with httpx.Client() as client:
        _send(client, payload, 1)
    logger.info("Live event sent.")


# ── CLI entry point ───────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="RecoverAI Webhook Simulator v2.0")
    parser.add_argument("--live",       action="store_true",  help="Send single live integration event")
    parser.add_argument("--count",      type=int,   default=None,  help="Stop after N synthetic events")
    parser.add_argument("--burst",      type=int,   default=None,  help="Send N events as fast as possible (sequential)")
    parser.add_argument("--chaos",      type=int,   default=None,  help="Chaos stress test: N concurrent HMAC-signed webhooks")
    parser.add_argument("--workers",    type=int,   default=64,    help="Thread pool size for --chaos (default 64)")
    parser.add_argument("--interval",   type=float, default=settings.simulator_interval_seconds)
    args = parser.parse_args()

    if args.live:
        _run_live()
    elif args.burst:
        _run_burst(args.burst)
    elif args.chaos:
        _run_chaos(args.chaos, args.workers)
    else:
        _run_synthetic(args.count, args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("\nSimulator stopped.")
        sys.exit(0)
