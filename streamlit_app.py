"""
RecoverAI Enterprise — Streamlit Cloud entry point.

This file IS the app. All UI lives here so Streamlit's script runner
renders it directly — no import relay, no exec, no runpy.

Tabs:
  1. Intelligence Hub       — KPIs, funnel, charts, audit ledger
  2. Payment Links          — Razorpay Payment Links API simulation
  3. Dispatch               — WhatsApp / SMS link dispatch
  4. HITL Approvals         — Human-in-the-Loop manual review queue
  5. A/B Testing            — Recovery strategy A/B engine
  6. Chaos Simulator        — Webhook stress & chaos testing
  7. Merchants              — Multi-tenant merchant isolation
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import sys
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal

# ── sys.path patch — must happen before any local imports ────────────────────
_ROOT = os.path.dirname(os.path.abspath(__file__))
_PKG  = os.path.join(_ROOT, "recover_ai")
for _p in (_PKG, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.chdir(_ROOT)

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from anomaly_detection import detect as detect_anomalies, forecast as forecast_anomalies
from explainability import beeswarm_figure, export_pdf, explain_transaction, feature_importance, waterfall_figure
from experimentation import choose_strategy, record as record_experiment, report as experiment_report
from merchant_copilot import answer as copilot_answer, stream_text, suggested_questions
from recovery_optimization import recommend as optimize_recovery, simulate as simulate_optimization

# ── Page config — first Streamlit call ───────────────────────────────────────
st.set_page_config(
    page_title="RecoverAI Enterprise",
    page_icon="🏦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Bootstrap DB + settings (once per session) ───────────────────────────────
@st.cache_resource(show_spinner="Initialising database…")
def _bootstrap():
    import database as db
    from config import get_settings
    db.init_db()
    return get_settings(), db

_settings, _db = _bootstrap()

# ── Design tokens ─────────────────────────────────────────────────────────────
C = dict(
    blue="#4285f4", green="#34a853", orange="#fbbc04",
    red="#ea4335",  purple="#ab47bc", teal="#26c6da",
    bg="#0d1b2a",   surface="#0a1628", border="#1e3a5f", text="#c9d1d9",
)
STATUS_COLOURS = {
    "FAILED": C["red"], "ML_SCORED": C["blue"],
    "LOW_PRIORITY_SKIP": C["purple"], "AGENT_EVALUATED": C["orange"],
    "ACTION_TRIGGERED": C["teal"], "RECOVERING": C["orange"],
    "RECOVERED": C["green"], "EXPIRED": C["purple"],
}
ROOT_CAUSE_COLOURS = {
    "GATEWAY_DOWN": C["blue"], "USER_CANCELLED": C["orange"],
    "INSUFFICIENT_FUNDS": C["red"], "NETWORK_TIMEOUT": C["teal"],
    "INVALID_DETAILS": C["purple"], "BANK_DECLINE": "#e91e63", "UNKNOWN": C["text"],
}
_PL = dict(
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(13,27,42,0.7)",
    font=dict(color=C["text"], family="Inter, sans-serif", size=12),
    margin=dict(l=16, r=16, t=38, b=16),
)

# ── Global CSS ────────────────────────────────────────────────────────────────
st.markdown(f"""<style>
html,body,.stApp{{background-color:{C['bg']} !important}}
section[data-testid="stSidebar"]{{background-color:{C['surface']} !important}}
.kpi-card{{background:linear-gradient(135deg,{C['border']} 0%,{C['surface']} 100%);
  border:1px solid {C['border']};border-radius:12px;padding:1.1rem 1.4rem;
  margin-bottom:.5rem;min-height:100px}}
.kpi-label{{color:{C['teal']};font-size:.72rem;text-transform:uppercase;letter-spacing:.1em}}
.kpi-value{{color:#fff;font-size:1.85rem;font-weight:700;margin:.15rem 0}}
.kpi-sub{{color:{C['text']};font-size:.76rem}}
.badge-ok{{background:#1a4731;color:{C['green']};border:1px solid {C['green']};
  border-radius:8px;padding:.3rem .85rem;font-weight:700;display:inline-block;font-size:.9rem}}
.badge-err{{background:#4a1010;color:{C['red']};border:1px solid {C['red']};
  border-radius:8px;padding:.3rem .85rem;font-weight:700;display:inline-block;font-size:.9rem}}
.tag{{border-radius:6px;padding:.15rem .6rem;font-size:.78rem;font-weight:600;display:inline-block}}
</style>""", unsafe_allow_html=True)

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🏦 RecoverAI Enterprise")
    st.caption(f"v{_settings.app_version} · {_settings.environment.upper()}")
    st.divider()
    if st.button("🔄 Force Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    live_feed = st.toggle(
        "📡 Live transaction feed",
        value=True,
        help="Generate one live-format failed payment on each refresh and run it through the recovery pipeline.",
    )
    refresh_seconds = st.slider(
        "Refresh interval (seconds)", min_value=2, max_value=60,
        value=max(2, min(60, int(_settings.dashboard_refresh_seconds))),
        disabled=not live_feed,
    )
    st.divider()
    st.caption(f"**DB:** `{_settings.database_path}`")
    st.caption(f"**ML threshold:** {_settings.ml_low_priority_threshold}")
    st.caption(f"**Max discount:** {_settings.max_discount_pct}%")
    st.divider()
    st.caption("**Stack:** SQLite WAL · LightGBM · SHA-256 · Streamlit · Plotly")

# ── Helpers ───────────────────────────────────────────────────────────────────
def _kpi(col, label, value, sub, colour="#ffffff"):
    col.markdown(
        f'<div class="kpi-card"><div class="kpi-label">{label}</div>'
        f'<div class="kpi-value" style="color:{colour}">{value}</div>'
        f'<div class="kpi-sub">{sub}</div></div>',
        unsafe_allow_html=True,
    )

def _tag(text, color):
    return f'<span class="tag" style="background:{color}22;color:{color};border:1px solid {color}">{text}</span>'

# ── Live transaction feed ───────────────────────────────────────────────────────
def _live_transaction_payload() -> dict[str, str | int | None]:
    """Create a Razorpay-shaped failed payment for the local live demo feed."""
    failure_code, failure_reason = random.choice([
        ("GATEWAY_ERROR", "bank_downtime"),
        ("NETWORK_TIMEOUT", "upstream_timeout"),
        ("INSUFFICIENT_FUNDS", "low_balance"),
        ("CARD_DECLINED", "issuer_declined"),
        ("INVALID_CARD", "invalid_details"),
        ("PAYMENT_CANCELLED", "user_cancelled"),
    ])
    return {
        "payment_id": f"pay_live_{uuid.uuid4().hex[:12]}",
        "order_id": f"order_live_{uuid.uuid4().hex[:12]}",
        "amount_paise": random.randint(50000, 1500000),
        "currency": "INR",
        "failure_code": failure_code,
        "failure_reason": failure_reason,
        "email_redacted": "live_user***@example.com",
    }


def _ingest_live_transaction() -> None:
    """Run one live-format event through the same pipeline used by the API."""
    from agent_engine import process_failed_payment
    payload = _live_transaction_payload()
    asyncio.run(process_failed_payment(**payload))
    st.cache_data.clear()


if live_feed:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=refresh_seconds * 1000, key="recoverai_live_refresh")
    try:
        _ingest_live_transaction()
    except Exception as exc:
        st.warning(f"Live feed paused: {exc}")


# ── Cached loaders ────────────────────────────────────────────────────────────
@st.cache_data(ttl=60)
def _load_summary():
    m = _db.get_summary_metrics()
    return {k: float(v) if isinstance(v, Decimal) else v for k, v in m.items()}

@st.cache_data(ttl=60)
def _load_funnel():
    return _db.get_funnel_counts()

@st.cache_data(ttl=60)
def _load_root_causes():
    return _db.get_root_cause_breakdown()

@st.cache_data(ttl=60)
def _load_timeseries():
    rows = _db.get_timeseries_data()
    if not rows:
        return pd.DataFrame(columns=["minute", "revenue_at_risk", "revenue_recovered"])
    df = pd.DataFrame([{"minute": r["minute"], "revenue_at_risk": float(r["revenue_at_risk"]),
                        "revenue_recovered": float(r["revenue_recovered"])} for r in rows])
    df["minute"] = pd.to_datetime(df["minute"])
    return df.sort_values("minute").reset_index(drop=True)

@st.cache_data(ttl=120)
def _load_audit_logs():
    rows = _db.get_audit_logs(100)
    return pd.DataFrame([dict(r) for r in rows]) if rows else pd.DataFrame()

@st.cache_data(ttl=60)
def _load_transactions():
    rows = _db.get_all_transactions(200)
    return pd.DataFrame([dict(r) for r in rows]) if rows else pd.DataFrame()

@st.cache_data(ttl=300)
def _load_ledger_status():
    try:
        return _db.verify_audit_integrity()
    except Exception:
        return False, "Verification error"

# ── Session-state defaults ────────────────────────────────────────────────────
for _k, _v in {
    "hitl_queue": [],
    "ab_results": {"control": {"sent": 0, "recovered": 0},
                   "variant": {"sent": 0, "recovered": 0}},
    "chaos_log": [],
    "payment_links": [],
    "dispatch_log": [],
    "merchants": {
        "MID_001": {"name": "Zomato", "plan": "Enterprise", "txns": 0, "recovered": 0},
        "MID_002": {"name": "Swiggy", "plan": "Growth",     "txns": 0, "recovered": 0},
        "MID_003": {"name": "Meesho", "plan": "Starter",    "txns": 0, "recovered": 0},
    },
}.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ══════════════════════════════════════════════════════════════════════════════
# TABS
# ══════════════════════════════════════════════════════════════════════════════
tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9, tab10, tab11, tab12 = st.tabs([
    "📊 Intelligence Hub", "🔗 Payment Links", "📨 Dispatch", "👤 HITL Approvals",
    "🧪 A/B Testing", "💥 Chaos Simulator", "🏢 Merchants", "🧠 Merchant Copilot",
    "🔍 Explainable AI", "🎯 Recovery Optimizer", "🧪 Experiment Agent", "🚨 Anomaly Agent",
])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — INTELLIGENCE HUB
# ══════════════════════════════════════════════════════════════════════════════
with tab1:
    st.markdown("## 🏦 RecoverAI Enterprise — Intelligence Hub")
    st.caption(f"Updated: {datetime.now().strftime('%H:%M:%S')} · Razorpay AI Buildathon · Track 03")

    summary         = _load_summary()
    total_risk      = summary.get("total_at_risk", 0.0)
    total_recovered = summary.get("total_recovered", 0.0)
    recovery_rate   = summary.get("recovery_rate", 0.0)
    avg_score       = summary.get("avg_recoverability_score", 0.0)
    _ledger_ok, _ledger_msg = _load_ledger_status()

    c1, c2, c3, c4, c5 = st.columns(5)
    _kpi(c1, "💰 Revenue at Risk",   f"₹{total_risk:,.0f}",      "All failed transactions")
    _kpi(c2, "✅ Revenue Recovered", f"₹{total_recovered:,.0f}", "Successfully recovered", C["green"])
    _kpi(c3, "📈 Recovery Rate",     f"{recovery_rate:.1f}%",    "Recovered / Total",
         C["green"] if recovery_rate >= 40 else C["orange"] if recovery_rate >= 20 else C["red"])
    _kpi(c4, "🤖 Avg ML Score",      f"{avg_score:.3f}",         "Recoverability confidence", C["teal"])
    badge = '<span class="badge-ok">🔒 VERIFIED</span>' if _ledger_ok else '<span class="badge-err">⚠️ ANOMALY</span>'
    c5.markdown(f'<div class="kpi-card"><div class="kpi-label">🔐 Audit Ledger</div>'
                f'<div style="margin-top:.5rem">{badge}</div>'
                f'<div class="kpi-sub" style="margin-top:.4rem">SHA-256 chain</div></div>',
                unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # Funnel + Donut
    col_f, col_d = st.columns([3, 2])
    with col_f:
        st.markdown("#### 🔽 ML-Augmented Recovery Funnel")
        funnel = _load_funnel()
        stages = ["Ingested", "ML Scored", "Agent Evaluated", "Action Triggered", "Recovered"]
        values = [funnel.get(k, 0) for k in ["ingested", "ml_scored", "agent_evaluated", "action_triggered", "recovered"]]
        fig_f = go.Figure(go.Funnel(
            y=stages, x=values, textposition="inside", textinfo="value+percent initial",
            marker=dict(color=[C["red"], C["blue"], C["orange"], C["teal"], C["green"]],
                        line=dict(width=1.5, color=C["bg"])),
            connector=dict(line=dict(color=C["border"], dash="dot", width=2)),
        ))
        fig_f.update_layout(**_PL, height=320)
        st.plotly_chart(fig_f, use_container_width=True)

    with col_d:
        st.markdown("#### 🍩 Failure Root Cause Breakdown")
        rc_data = _load_root_causes()
        if rc_data:
            labels = [r["failure_category"] for r in rc_data]
            vals   = [r["count"] for r in rc_data]
            clrs   = [ROOT_CAUSE_COLOURS.get(lb, C["teal"]) for lb in labels]
            fig_d  = go.Figure(go.Pie(
                labels=labels, values=vals, hole=0.56,
                marker=dict(colors=clrs, line=dict(color=C["bg"], width=2)),
                textinfo="label+percent", textfont=dict(size=11),
            ))
            fig_d.update_layout(**_PL, height=320, showlegend=True,
                                legend=dict(orientation="h", y=-0.18),
                                annotations=[dict(text=f"<b>{sum(vals)}</b><br>Total",
                                                  x=0.5, y=0.5, font=dict(size=13, color="#fff"),
                                                  showarrow=False)])
            st.plotly_chart(fig_d, use_container_width=True)
        else:
            st.info("Waiting for transaction data.", icon="⏳")

    # Time-series
    st.markdown("#### 📊 Revenue at Risk vs. Recovered — Live Time-Series")
    ts_df = _load_timeseries()
    if not ts_df.empty:
        fig_ts = go.Figure()
        fig_ts.add_trace(go.Scatter(x=ts_df["minute"], y=ts_df["revenue_at_risk"],
            name="Revenue at Risk", mode="lines", line=dict(color=C["red"], width=2.5),
            fill="tozeroy", fillcolor="rgba(234,67,53,0.12)"))
        fig_ts.add_trace(go.Scatter(x=ts_df["minute"], y=ts_df["revenue_recovered"],
            name="Revenue Recovered", mode="lines", line=dict(color=C["green"], width=2.5),
            fill="tozeroy", fillcolor="rgba(52,168,83,0.18)"))
        fig_ts.update_layout(**_PL, height=360, hovermode="x unified",
            xaxis=dict(gridcolor=C["border"]),
            yaxis=dict(gridcolor=C["border"], tickprefix="₹", tickformat=",.0f"),
            legend=dict(orientation="h", y=1.06, bgcolor="rgba(0,0,0,0)"))
        st.plotly_chart(fig_ts, use_container_width=True)
    else:
        st.info("No time-series data yet. Seed transactions to see the chart.", icon="📡")

    # Histogram + Status bar
    txn_df = _load_transactions()
    col_h, col_b = st.columns(2)
    with col_h:
        st.markdown("#### 🤖 ML Score Distribution")
        if not txn_df.empty and "recoverability_score" in txn_df.columns:
            scores = txn_df["recoverability_score"].dropna()
            scores = scores[scores > 0]
            if not scores.empty:
                fig_h = go.Figure(go.Histogram(x=scores, nbinsx=25, marker_color=C["blue"]))
                fig_h.add_vline(x=_settings.ml_low_priority_threshold, line_dash="dash",
                                line_color=C["red"], line_width=2)
                fig_h.update_layout(**_PL, height=240, showlegend=False,
                                    xaxis=dict(gridcolor=C["border"]),
                                    yaxis=dict(gridcolor=C["border"]))
                st.plotly_chart(fig_h, use_container_width=True)
            else:
                st.info("No scored transactions yet.", icon="⚙️")
        else:
            st.info("No transaction data yet.", icon="⚙️")

    with col_b:
        st.markdown("#### 📋 Status Distribution")
        if not txn_df.empty:
            sc = txn_df["status"].value_counts().reset_index()
            sc.columns = ["status", "count"]
            fig_b = go.Figure(go.Bar(
                x=sc["status"], y=sc["count"],
                marker_color=[STATUS_COLOURS.get(s, C["teal"]) for s in sc["status"]],
                text=sc["count"], textposition="outside"))
            fig_b.update_layout(**_PL, height=240, showlegend=False,
                                xaxis=dict(gridcolor=C["border"]),
                                yaxis=dict(gridcolor=C["border"]))
            st.plotly_chart(fig_b, use_container_width=True)
        else:
            st.info("No transaction data yet.", icon="📋")

    # Ledger
    st.markdown("---")
    st.markdown("#### 🔐 Cryptographic Audit Ledger Verification")
    if st.button("🔍 Verify Ledger Integrity", type="primary", key="verify_ledger"):
        with st.spinner("Replaying SHA-256 hash chain…"):
            _load_ledger_status.clear()
            ok, msg = _load_ledger_status()
        if ok:
            st.markdown('<div class="badge-ok" style="font-size:1rem;padding:.5rem 1.2rem">🔒 100% IMMUTABLE & VERIFIED</div>', unsafe_allow_html=True)
            st.success(msg)
        else:
            st.markdown('<div class="badge-err" style="font-size:1rem;padding:.5rem 1.2rem">⚠️ TAMPER DETECTED</div>', unsafe_allow_html=True)
            st.error(msg)

    with st.expander("📜 Audit Trail — Immutable SHA-256 Hash-Chain Log"):
        audit_df = _load_audit_logs()
        if not audit_df.empty:
            disp = [c for c in ["timestamp","transaction_id","action_taken","source",
                                 "recoverability_score","amount_paise","decision_rationale","current_hash"]
                    if c in audit_df.columns]
            adf = audit_df[disp].copy()
            if "amount_paise" in adf.columns:
                adf["amount"] = (adf["amount_paise"] / 100).map(lambda x: f"₹{x:,.2f}")
                adf.drop(columns=["amount_paise"], inplace=True)
            if "current_hash" in adf.columns:
                adf["hash"] = adf["current_hash"].str[:16] + "…"
                adf.drop(columns=["current_hash"], inplace=True)
            st.dataframe(adf, use_container_width=True, height=320)
            st.caption(f"{len(audit_df)} records")
        else:
            st.info("No audit records yet.", icon="📝")

    st.divider()
    st.caption("RecoverAI Enterprise v2.0.0 · Razorpay AI Buildathon Track 03 · SQLite WAL · LightGBM · SHA-256 · Plotly")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — RAZORPAY PAYMENT LINKS
# ══════════════════════════════════════════════════════════════════════════════
with tab2:
    st.markdown("## 🔗 Razorpay Payment Links API")
    st.caption("Simulate creating and managing Razorpay Payment Links for failed transactions")

    col_form, col_list = st.columns([1, 2])

    with col_form:
        st.markdown("#### Create Payment Link")
        with st.form("pl_form"):
            pl_amount    = st.number_input("Amount (₹)", min_value=1, max_value=100000, value=500)
            pl_desc      = st.text_input("Description", value="Payment recovery link")
            pl_expiry    = st.number_input("Expires in (minutes)", min_value=5, max_value=1440, value=60)
            pl_customer  = st.text_input("Customer email / phone", value="customer@example.com")
            pl_txn_ref   = st.text_input("Transaction ref (payment_id)", value=f"pay_{uuid.uuid4().hex[:10]}")
            pl_submitted = st.form_submit_button("🔗 Create Link", type="primary", use_container_width=True)

        if pl_submitted:
            link_id = f"plink_{uuid.uuid4().hex[:12]}"
            link_url = f"https://rzp.io/i/{uuid.uuid4().hex[:8]}"
            link = {
                "id":          link_id,
                "amount":      pl_amount,
                "description": pl_desc,
                "customer":    pl_customer,
                "txn_ref":     pl_txn_ref,
                "url":         link_url,
                "status":      "created",
                "expires_in":  f"{pl_expiry} min",
                "created_at":  datetime.now().strftime("%H:%M:%S"),
            }
            st.session_state.payment_links.insert(0, link)
            st.success(f"✅ Link created: `{link_url}`")

        st.markdown("#### Razorpay API Config")
        rz_key = st.text_input("API Key ID", value="rzp_test_xxxxxxxxxxxx", type="password")
        st.caption("Keys are never stored — demo only.")

        if st.button("🧪 Bulk Generate (5 links)", use_container_width=True):
            statuses = ["created", "paid", "expired", "cancelled"]
            for _ in range(5):
                amt = random.randint(200, 5000)
                st.session_state.payment_links.insert(0, {
                    "id":          f"plink_{uuid.uuid4().hex[:12]}",
                    "amount":      amt,
                    "description": random.choice(["Retry payment", "Recovery link", "Order #" + str(random.randint(1000,9999))]),
                    "customer":    f"user{random.randint(100,999)}@example.com",
                    "txn_ref":     f"pay_{uuid.uuid4().hex[:10]}",
                    "url":         f"https://rzp.io/i/{uuid.uuid4().hex[:8]}",
                    "status":      random.choice(statuses),
                    "expires_in":  f"{random.choice([15,30,60,120])} min",
                    "created_at":  datetime.now().strftime("%H:%M:%S"),
                })
            st.success("5 payment links generated.")
            st.rerun()

    with col_list:
        st.markdown("#### Active Payment Links")
        links = st.session_state.payment_links
        if not links:
            st.info("No payment links yet. Create one on the left.", icon="🔗")
        else:
            status_colours_pl = {"created": C["blue"], "paid": C["green"],
                                  "expired": C["orange"], "cancelled": C["red"]}
            for lnk in links[:20]:
                sc = status_colours_pl.get(lnk["status"], C["teal"])
                with st.container():
                    ca, cb, cc = st.columns([2, 2, 1])
                    ca.markdown(f"**₹{lnk['amount']:,}** — {lnk['description']}<br>"
                                f"<small>{lnk['customer']} · {lnk['created_at']}</small>",
                                unsafe_allow_html=True)
                    cb.markdown(f"`{lnk['url']}`<br><small>Ref: {lnk['txn_ref']}</small>",
                                unsafe_allow_html=True)
                    cc.markdown(_tag(lnk["status"].upper(), sc), unsafe_allow_html=True)
                    st.divider()

        # Summary metrics
        if links:
            total   = len(links)
            paid    = sum(1 for l in links if l["status"] == "paid")
            expired = sum(1 for l in links if l["status"] == "expired")
            m1, m2, m3 = st.columns(3)
            m1.metric("Total Links",  total)
            m2.metric("Paid",         paid,    delta=f"{paid/total*100:.0f}%" if total else "0%")
            m3.metric("Expired",      expired, delta=f"-{expired/total*100:.0f}%" if total else "0%")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — WHATSAPP / SMS DISPATCH
# ══════════════════════════════════════════════════════════════════════════════
with tab3:
    st.markdown("## 📨 WhatsApp / SMS Payment Link Dispatch")
    st.caption("Simulate sending payment recovery links via WhatsApp Business API and SMS gateway")

    col_d1, col_d2 = st.columns([1, 2])

    with col_d1:
        st.markdown("#### Send Recovery Link")
        channel = st.radio("Channel", ["📱 WhatsApp", "💬 SMS", "📧 Email"], horizontal=True)
        with st.form("dispatch_form"):
            d_recipient = st.text_input("Recipient (phone / email)", value="+91-98765-43210")
            d_amount    = st.number_input("Amount (₹)", min_value=1, max_value=100000, value=999)
            d_link      = st.text_input("Payment link", value=f"https://rzp.io/i/{uuid.uuid4().hex[:8]}")
            d_template  = st.selectbox("Message template", [
                "Hey {name}, your payment of ₹{amount} failed. Complete it here: {link}",
                "Reminder: ₹{amount} pending. Pay now: {link} (expires in 1hr)",
                "Your order is on hold! Pay ₹{amount} to confirm: {link}",
            ])
            d_submitted = st.form_submit_button("📤 Dispatch", type="primary", use_container_width=True)

        if d_submitted:
            ch_icon = {"📱 WhatsApp": "WhatsApp", "💬 SMS": "SMS", "📧 Email": "Email"}[channel]
            status  = random.choice(["DELIVERED", "DELIVERED", "DELIVERED", "PENDING", "FAILED"])
            st.session_state.dispatch_log.insert(0, {
                "channel":   ch_icon,
                "recipient": d_recipient,
                "amount":    d_amount,
                "link":      d_link,
                "status":    status,
                "sent_at":   datetime.now().strftime("%H:%M:%S"),
                "message":   d_template.replace("{amount}", str(d_amount)).replace("{link}", d_link).replace("{name}", "Customer"),
            })
            colour = C["green"] if status == "DELIVERED" else C["orange"] if status == "PENDING" else C["red"]
            st.markdown(_tag(status, colour), unsafe_allow_html=True)

        if st.button("🔁 Bulk Dispatch (10 simulated)", use_container_width=True):
            channels = ["WhatsApp", "SMS", "Email"]
            statuses = ["DELIVERED", "DELIVERED", "DELIVERED", "PENDING", "FAILED"]
            for _ in range(10):
                amt = random.randint(100, 5000)
                st.session_state.dispatch_log.insert(0, {
                    "channel":   random.choice(channels),
                    "recipient": f"+91-{random.randint(70000,99999)}-{random.randint(10000,99999)}",
                    "amount":    amt,
                    "link":      f"https://rzp.io/i/{uuid.uuid4().hex[:8]}",
                    "status":    random.choice(statuses),
                    "sent_at":   datetime.now().strftime("%H:%M:%S"),
                    "message":   f"Recovery link for ₹{amt}",
                })
            st.rerun()

    with col_d2:
        st.markdown("#### Dispatch Log")
        log = st.session_state.dispatch_log
        if not log:
            st.info("No dispatches yet.", icon="📨")
        else:
            status_c = {"DELIVERED": C["green"], "PENDING": C["orange"], "FAILED": C["red"]}
            channel_icons = {"WhatsApp": "📱", "SMS": "💬", "Email": "📧"}
            for entry in log[:30]:
                sc = status_c.get(entry["status"], C["text"])
                ci = channel_icons.get(entry["channel"], "📤")
                a, b, c = st.columns([2, 3, 1])
                a.markdown(f"{ci} **{entry['channel']}** · {entry['sent_at']}<br>"
                           f"<small>{entry['recipient']}</small>", unsafe_allow_html=True)
                b.markdown(f"₹{entry['amount']:,} · <small>{entry['message'][:60]}…</small>",
                           unsafe_allow_html=True)
                c.markdown(_tag(entry["status"], sc), unsafe_allow_html=True)
                st.divider()

        if log:
            total = len(log)
            dlvd  = sum(1 for e in log if e["status"] == "DELIVERED")
            ch_counts = {}
            for e in log:
                ch_counts[e["channel"]] = ch_counts.get(e["channel"], 0) + 1
            st.markdown("**Channel breakdown:**  " +
                        "  ".join(f"`{k}:{v}`" for k, v in ch_counts.items()))
            st.metric("Delivery Rate", f"{dlvd/total*100:.0f}%" if total else "0%")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — HITL MANUAL APPROVALS
# ══════════════════════════════════════════════════════════════════════════════
with tab4:
    st.markdown("## 👤 Human-in-the-Loop (HITL) Approval Queue")
    st.caption("High-value or ambiguous transactions that require manual agent review before recovery action")

    col_q1, col_q2 = st.columns([1, 2])

    with col_q1:
        st.markdown("#### Add to Queue")
        with st.form("hitl_form"):
            h_txn    = st.text_input("Transaction ID", value=f"pay_{uuid.uuid4().hex[:10]}")
            h_amount = st.number_input("Amount (₹)", min_value=100, max_value=500000, value=12500)
            h_reason = st.selectbox("Escalation reason", [
                "High value (>₹10,000)",
                "Repeated failure (3+ attempts)",
                "Suspected fraud signal",
                "VIP customer",
                "ML score ambiguous (0.4–0.6)",
            ])
            h_score  = st.slider("ML Score", 0.0, 1.0, 0.52, 0.01)
            h_submit = st.form_submit_button("➕ Add to Queue", use_container_width=True)

        if h_submit:
            st.session_state.hitl_queue.insert(0, {
                "txn_id":  h_txn,
                "amount":  h_amount,
                "reason":  h_reason,
                "score":   h_score,
                "status":  "PENDING",
                "added_at": datetime.now().strftime("%H:%M:%S"),
                "notes":   "",
            })
            st.success(f"Added `{h_txn}` to HITL queue.")

        if st.button("🎲 Seed 5 random cases", use_container_width=True):
            reasons = ["High value (>₹10,000)", "Repeated failure (3+ attempts)",
                       "Suspected fraud signal", "VIP customer", "ML score ambiguous (0.4–0.6)"]
            for _ in range(5):
                st.session_state.hitl_queue.insert(0, {
                    "txn_id":  f"pay_{uuid.uuid4().hex[:10]}",
                    "amount":  random.randint(5000, 50000),
                    "reason":  random.choice(reasons),
                    "score":   round(random.uniform(0.35, 0.65), 3),
                    "status":  "PENDING",
                    "added_at": datetime.now().strftime("%H:%M:%S"),
                    "notes":   "",
                })
            st.rerun()

        pending = sum(1 for i in st.session_state.hitl_queue if i["status"] == "PENDING")
        approved = sum(1 for i in st.session_state.hitl_queue if i["status"] == "APPROVED")
        rejected = sum(1 for i in st.session_state.hitl_queue if i["status"] == "REJECTED")
        m1, m2, m3 = st.columns(3)
        m1.metric("Pending",  pending,  delta=f"-{pending}" if pending > 5 else None)
        m2.metric("Approved", approved)
        m3.metric("Rejected", rejected)

    with col_q2:
        st.markdown("#### Review Queue")
        queue = st.session_state.hitl_queue
        if not queue:
            st.info("Queue is empty. Add items on the left.", icon="👤")
        else:
            for i, item in enumerate(queue[:15]):
                if item["status"] != "PENDING":
                    continue
                sc = C["orange"] if item["score"] < 0.5 else C["teal"]
                with st.expander(f"🔔 {item['txn_id']} — ₹{item['amount']:,} · Score: {item['score']:.3f}"):
                    st.markdown(f"**Reason:** {item['reason']}  \n"
                                f"**Added:** {item['added_at']}  \n"
                                f"**ML Score:** {_tag(str(item['score']), sc)}", unsafe_allow_html=True)
                    notes = st.text_input("Agent notes", key=f"notes_{i}", placeholder="Optional notes…")
                    ba, br = st.columns(2)
                    if ba.button("✅ Approve", key=f"approve_{i}", type="primary"):
                        st.session_state.hitl_queue[i]["status"] = "APPROVED"
                        st.session_state.hitl_queue[i]["notes"]  = notes
                        st.rerun()
                    if br.button("❌ Reject", key=f"reject_{i}"):
                        st.session_state.hitl_queue[i]["status"] = "REJECTED"
                        st.session_state.hitl_queue[i]["notes"]  = notes
                        st.rerun()

            # Resolved items
            resolved = [it for it in queue if it["status"] != "PENDING"]
            if resolved:
                st.markdown("#### Resolved Cases")
                rows_r = []
                for it in resolved[:20]:
                    rows_r.append({"TXN ID": it["txn_id"], "Amount": f"₹{it['amount']:,}",
                                   "Score": it["score"], "Decision": it["status"],
                                   "Reason": it["reason"], "Notes": it["notes"]})
                st.dataframe(pd.DataFrame(rows_r), use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — A/B TESTING ENGINE
# ══════════════════════════════════════════════════════════════════════════════
with tab5:
    st.markdown("## 🧪 A/B Testing Engine — Recovery Strategies")
    st.caption("Run controlled experiments to compare recovery strategies and measure statistical lift")

    col_a1, col_a2 = st.columns([1, 2])

    with col_a1:
        st.markdown("#### Experiment Config")
        exp_name    = st.text_input("Experiment name", value="Discount vs Retry Link")
        ctrl_strat  = st.selectbox("Control strategy", [
            "Direct payment link (no discount)",
            "Standard retry email",
            "SMS reminder only",
        ])
        var_strat   = st.selectbox("Variant strategy", [
            "5% discount payment link",
            "10% discount + WhatsApp",
            "Personalised email + retry link",
            "Free shipping offer + link",
        ])
        split_pct   = st.slider("Variant traffic split %", 10, 50, 30)

        if st.button("▶️ Simulate 50 transactions", type="primary", use_container_width=True):
            ab = st.session_state.ab_results
            for _ in range(50):
                is_variant = random.random() < (split_pct / 100)
                recovered  = random.random() < (0.38 if not is_variant else 0.52)
                bucket     = "variant" if is_variant else "control"
                ab[bucket]["sent"] += 1
                if recovered:
                    ab[bucket]["recovered"] += 1
            st.success("Simulated 50 transactions across both arms.")
            st.rerun()

        if st.button("🔄 Reset experiment", use_container_width=True):
            st.session_state.ab_results = {
                "control": {"sent": 0, "recovered": 0},
                "variant": {"sent": 0, "recovered": 0},
            }
            st.rerun()

    with col_a2:
        st.markdown("#### Live Results")
        ab = st.session_state.ab_results
        ctrl = ab["control"]
        var  = ab["variant"]

        ctrl_rate = ctrl["recovered"] / ctrl["sent"] if ctrl["sent"] else 0
        var_rate  = var["recovered"]  / var["sent"]  if var["sent"]  else 0
        lift      = ((var_rate - ctrl_rate) / ctrl_rate * 100) if ctrl_rate > 0 else 0

        mc1, mc2, mc3, mc4 = st.columns(4)
        mc1.metric("Control sent",      ctrl["sent"])
        mc2.metric("Control recovery",  f"{ctrl_rate*100:.1f}%")
        mc3.metric("Variant sent",      var["sent"])
        mc4.metric("Variant recovery",  f"{var_rate*100:.1f}%",
                   delta=f"+{lift:.1f}% lift" if lift > 0 else f"{lift:.1f}% lift")

        if ctrl["sent"] > 0 and var["sent"] > 0:
            fig_ab = go.Figure()
            for bucket, data, colour in [("Control", ctrl, C["blue"]), ("Variant", var, C["green"])]:
                rate = data["recovered"] / data["sent"] if data["sent"] else 0
                fig_ab.add_trace(go.Bar(
                    name=bucket,
                    x=[bucket],
                    y=[rate * 100],
                    marker_color=colour,
                    text=[f"{rate*100:.1f}%"],
                    textposition="outside",
                ))
            fig_ab.update_layout(**_PL, height=300,
                                 yaxis=dict(title="Recovery Rate %", gridcolor=C["border"]),
                                 showlegend=False)
            st.plotly_chart(fig_ab, use_container_width=True)

            # Statistical significance (simple z-test approximation)
            import math
            n1, p1 = ctrl["sent"], ctrl_rate
            n2, p2 = var["sent"], var_rate
            if n1 > 10 and n2 > 10:
                p_pool = (ctrl["recovered"] + var["recovered"]) / (n1 + n2)
                se     = math.sqrt(p_pool * (1 - p_pool) * (1/n1 + 1/n2))
                z      = (p2 - p1) / se if se > 0 else 0
                sig    = abs(z) > 1.96
                st.markdown(
                    f"**Z-score:** `{z:.2f}`  \n"
                    f"**Statistically significant (95% CI):** {'✅ Yes' if sig else '⏳ Not yet'}  \n"
                    f"**Strategy:** {ctrl_strat} vs {var_strat}"
                )
        else:
            st.info("Click 'Simulate 50 transactions' to start the experiment.", icon="🧪")

        st.markdown("#### Experiment History")
        hist_data = [
            {"Name": "Discount 5% vs No Discount",   "Control": "34.2%", "Variant": "51.8%", "Lift": "+51.5%", "Sig": "✅"},
            {"Name": "SMS vs Email retry",            "Control": "28.1%", "Variant": "33.4%", "Lift": "+18.9%", "Sig": "✅"},
            {"Name": "Instant link vs 1hr delay",     "Control": "41.0%", "Variant": "38.5%", "Lift": "-6.1%",  "Sig": "❌"},
            {"Name": "WhatsApp vs SMS dispatch",      "Control": "35.5%", "Variant": "49.2%", "Lift": "+38.6%", "Sig": "✅"},
        ]
        st.dataframe(pd.DataFrame(hist_data), use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 6 — CHAOS & STRESS SIMULATOR
# ══════════════════════════════════════════════════════════════════════════════
with tab6:
    st.markdown("## 💥 Live Webhook Chaos & Stress Test Simulator")
    st.caption("Inject failures, latency spikes, and malformed payloads to test system resilience")

    col_c1, col_c2 = st.columns([1, 2])

    with col_c1:
        st.markdown("#### Chaos Configuration")
        n_events      = st.slider("Events to generate", 5, 100, 20)
        failure_rate  = st.slider("Failure injection rate %", 0, 100, 30)
        latency_spike = st.slider("Latency spike (ms)", 0, 5000, 500)
        chaos_modes   = st.multiselect("Chaos modes", [
            "Random payload corruption",
            "Duplicate event injection",
            "Out-of-order delivery",
            "Missing required fields",
            "Oversized payload (>1MB)",
            "Invalid HMAC signature",
            "Network timeout simulation",
        ], default=["Random payload corruption", "Duplicate event injection"])

        col_cc1, col_cc2 = st.columns(2)
        run_chaos = col_cc1.button("💥 Run Chaos", type="primary", use_container_width=True)
        clr_chaos = col_cc2.button("🗑 Clear Log", use_container_width=True)

        if clr_chaos:
            st.session_state.chaos_log = []
            st.rerun()

        if run_chaos:
            error_types = ["GATEWAY_DOWN", "INSUFFICIENT_FUNDS", "NETWORK_TIMEOUT",
                           "BANK_DECLINE", "INVALID_DETAILS", "USER_CANCELLED"]
            for i in range(n_events):
                injected = random.random() < (failure_rate / 100)
                chaos_type = random.choice(chaos_modes) if (injected and chaos_modes) else None
                latency    = latency_spike + random.randint(-100, 200) if injected else random.randint(10, 80)
                status     = random.choice(["PROCESSED", "PROCESSED", "REJECTED", "TIMEOUT"]) if injected else "PROCESSED"
                st.session_state.chaos_log.insert(0, {
                    "event_id":   f"evt_{uuid.uuid4().hex[:8]}",
                    "type":       random.choice(error_types),
                    "amount":     random.randint(100, 20000),
                    "latency_ms": max(0, latency),
                    "status":     status,
                    "chaos":      chaos_type or "—",
                    "injected":   injected,
                    "time":       datetime.now().strftime("%H:%M:%S.") + f"{random.randint(0,999):03d}",
                })
            st.success(f"Generated {n_events} events.")
            st.rerun()

        # Chaos metrics
        log = st.session_state.chaos_log
        if log:
            total_e    = len(log)
            injected_e = sum(1 for e in log if e["injected"])
            processed  = sum(1 for e in log if e["status"] == "PROCESSED")
            avg_lat    = sum(e["latency_ms"] for e in log) / total_e if total_e else 0
            m1, m2 = st.columns(2)
            m1.metric("Total events",    total_e)
            m1.metric("Chaos injected",  injected_e)
            m2.metric("Processed",       processed)
            m2.metric("Avg latency",     f"{avg_lat:.0f} ms")

    with col_c2:
        st.markdown("#### Event Stream")
        log = st.session_state.chaos_log
        if not log:
            st.info("Run the chaos simulator to see events here.", icon="💥")
        else:
            status_c = {"PROCESSED": C["green"], "REJECTED": C["red"],
                        "TIMEOUT": C["orange"]}
            for evt in log[:40]:
                sc = status_c.get(evt["status"], C["text"])
                inj_tag = _tag("CHAOS", C["red"]) if evt["injected"] else _tag("CLEAN", C["green"])
                a, b, c, d = st.columns([2, 2, 1, 1])
                a.markdown(f"`{evt['event_id']}` · {evt['time']}<br>"
                           f"<small>{evt['type']}</small>", unsafe_allow_html=True)
                b.markdown(f"₹{evt['amount']:,} · {evt['latency_ms']}ms<br>"
                           f"<small>Chaos: {evt['chaos']}</small>", unsafe_allow_html=True)
                c.markdown(_tag(evt["status"], sc), unsafe_allow_html=True)
                d.markdown(inj_tag, unsafe_allow_html=True)
                st.divider()

            # Latency distribution chart
            if len(log) >= 5:
                st.markdown("#### Latency Distribution")
                latencies = [e["latency_ms"] for e in log]
                fig_lat = go.Figure(go.Histogram(x=latencies, nbinsx=20,
                                                  marker_color=C["teal"]))
                fig_lat.add_vline(x=sum(latencies)/len(latencies), line_dash="dash",
                                  line_color=C["orange"], line_width=2,
                                  annotation_text="avg")
                fig_lat.update_layout(**_PL, height=220, showlegend=False,
                                      xaxis=dict(title="Latency (ms)", gridcolor=C["border"]),
                                      yaxis=dict(gridcolor=C["border"]))
                st.plotly_chart(fig_lat, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 7 — MULTI-TENANT MERCHANTS
# ══════════════════════════════════════════════════════════════════════════════
with tab7:
    st.markdown("## 🏢 Multi-Tenant Merchant Isolation")
    st.caption("Isolated merchant accounts with per-tenant data, configuration, and recovery metrics")

    col_m1, col_m2 = st.columns([1, 2])

    with col_m1:
        st.markdown("#### Merchant Registry")

        with st.form("add_merchant_form"):
            m_id   = st.text_input("Merchant ID", value=f"MID_{uuid.uuid4().hex[:4].upper()}")
            m_name = st.text_input("Merchant name", value="")
            m_plan = st.selectbox("Plan", ["Starter", "Growth", "Enterprise"])
            m_add  = st.form_submit_button("➕ Register Merchant", use_container_width=True)

        if m_add and m_name:
            st.session_state.merchants[m_id] = {
                "name": m_name, "plan": m_plan, "txns": 0, "recovered": 0
            }
            st.success(f"Registered **{m_name}** (`{m_id}`)")

        if st.button("📊 Simulate activity for all merchants", use_container_width=True):
            for mid in st.session_state.merchants:
                extra_txns = random.randint(5, 30)
                extra_rec  = random.randint(1, extra_txns)
                st.session_state.merchants[mid]["txns"]      += extra_txns
                st.session_state.merchants[mid]["recovered"] += extra_rec
            st.rerun()

        plan_colours = {"Starter": C["text"], "Growth": C["blue"], "Enterprise": C["teal"]}
        for mid, m in st.session_state.merchants.items():
            pc = plan_colours.get(m["plan"], C["text"])
            st.markdown(
                f"**{m['name']}** `{mid}`  \n"
                f"{_tag(m['plan'], pc)} · {m['txns']} txns · "
                f"{m['recovered']} recovered",
                unsafe_allow_html=True,
            )
            st.divider()

    with col_m2:
        st.markdown("#### Per-Tenant Dashboard")

        merchants = st.session_state.merchants
        if not merchants:
            st.info("No merchants registered.", icon="🏢")
        else:
            selected_mid = st.selectbox("Select merchant", list(merchants.keys()),
                                        format_func=lambda k: f"{merchants[k]['name']} ({k})")
            m = merchants[selected_mid]
            rate = (m["recovered"] / m["txns"] * 100) if m["txns"] > 0 else 0.0

            km1, km2, km3, km4 = st.columns(4)
            km1.metric("Merchant",       m["name"])
            km2.metric("Plan",           m["plan"])
            km3.metric("Transactions",   m["txns"])
            km4.metric("Recovery Rate",  f"{rate:.1f}%")

            # Simulated per-tenant time-series
            st.markdown("#### Simulated Revenue Recovery Timeline")
            hours   = list(range(24))
            at_risk = [random.randint(1000, 8000) for _ in hours]
            rec     = [int(v * random.uniform(0.25, 0.6)) for v in at_risk]
            fig_m = go.Figure()
            fig_m.add_trace(go.Scatter(x=hours, y=at_risk, name="At Risk",
                                       mode="lines+markers",
                                       line=dict(color=C["red"], width=2)))
            fig_m.add_trace(go.Scatter(x=hours, y=rec, name="Recovered",
                                       mode="lines+markers",
                                       line=dict(color=C["green"], width=2)))
            fig_m.update_layout(**_PL, height=280, hovermode="x unified",
                                xaxis=dict(title="Hour of day", gridcolor=C["border"]),
                                yaxis=dict(title="₹", gridcolor=C["border"]),
                                legend=dict(orientation="h", y=1.1))
            st.plotly_chart(fig_m, use_container_width=True)

            # All merchants comparison
            st.markdown("#### All Merchants — Recovery Comparison")
            names  = [v["name"] for v in merchants.values()]
            rates  = [(v["recovered"] / v["txns"] * 100) if v["txns"] > 0 else 0.0
                      for v in merchants.values()]
            colours_m = [plan_colours.get(v["plan"], C["text"]) for v in merchants.values()]
            fig_comp = go.Figure(go.Bar(
                x=names, y=rates,
                marker_color=colours_m,
                text=[f"{r:.1f}%" for r in rates],
                textposition="outside",
            ))
            fig_comp.update_layout(**_PL, height=260,
                                   yaxis=dict(title="Recovery %", gridcolor=C["border"]),
                                   showlegend=False)
            st.plotly_chart(fig_comp, use_container_width=True)

            # Isolation audit
            st.markdown("#### Tenant Isolation Audit")
            st.dataframe(pd.DataFrame([
                {"Merchant": v["name"], "ID": mid, "Plan": v["plan"],
                 "DB Namespace": f"tenant_{mid.lower()}",
                 "API Scope": f"rzp_{mid.lower()}_*",
                 "Data Isolation": "✅ Enforced",
                 "Rate Limit": "1000 req/min" if v["plan"] == "Enterprise"
                               else "200 req/min" if v["plan"] == "Growth" else "50 req/min"}
                for mid, v in merchants.items()
            ]), use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 8 — MERCHANT COPILOT
# ══════════════════════════════════════════════════════════════════════════════
with tab8:
    st.markdown("## 🧠 Merchant Copilot")
    st.caption("Ask questions over transactions, audit logs, recovery actions, and financial metrics.")
    if "copilot_messages" not in st.session_state:
        st.session_state.copilot_messages = []
    st.markdown("#### Suggested questions")
    qcols = st.columns(4)
    for idx, question in enumerate(suggested_questions()):
        if qcols[idx].button(question, key=f"copilot_suggestion_{idx}", use_container_width=True):
            st.session_state.copilot_pending = question
    prompt = st.chat_input("Ask about revenue at risk, recovery rate, root causes, or audit actions")
    prompt = prompt or st.session_state.pop("copilot_pending", None)
    for message in st.session_state.copilot_messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message.get("sql"):
                with st.expander("Validated SQL and evidence"):
                    st.code(message["sql"], language="sql")
                    if message.get("rows"):
                        st.dataframe(pd.DataFrame(message["rows"]), use_container_width=True)
    if prompt:
        st.session_state.copilot_messages.append({"role": "user", "content": prompt})
        result = copilot_answer(prompt)
        with st.chat_message("assistant"):
            response_box = st.empty()
            accumulated = ""
            for chunk in stream_text(result["answer"]):
                accumulated += chunk
                response_box.markdown(accumulated + "▌")
            response_box.markdown(accumulated)
            with st.expander("Validated SQL and evidence"):
                st.code(result["sql"], language="sql")
                if result["rows"]:
                    st.dataframe(pd.DataFrame(result["rows"]), use_container_width=True)
        st.session_state.copilot_messages.append({"role": "assistant", "content": accumulated, "sql": result["sql"], "rows": result["rows"]})

# ══════════════════════════════════════════════════════════════════════════════
# TAB 9 — EXPLAINABLE AI
# ══════════════════════════════════════════════════════════════════════════════
with tab9:
    st.markdown("## 🔍 Explainable AI")
    explain_rows = [dict(row) for row in _db.get_all_transactions(500)]
    if not explain_rows:
        st.info("No scored transactions are available for explanation yet.")
    else:
        ids = [row["payment_id"] for row in explain_rows]
        selected_id = st.selectbox("Transaction to explain", ids)
        selected = next(row for row in explain_rows if row["payment_id"] == selected_id)
        explanation = explain_transaction(selected)
        e1, e2, e3 = st.columns(3)
        e1.metric("Recovery score", f"{explanation['score']:.3f}")
        e2.metric("Recommended action", explanation["action"])
        e3.metric("Root cause", selected.get("failure_category") or "UNKNOWN")
        st.info(explanation["action_reason"])
        st.pyplot(waterfall_figure(explanation), clear_figure=True)
        pdf = export_pdf(explanation)
        st.download_button("Download transaction explanation PDF", pdf, file_name=f"recoverai_{selected_id}_explanation.pdf", mime="application/pdf")
        st.markdown("#### Feature importance across transactions")
        imp = feature_importance(explain_rows)
        st.bar_chart(imp.set_index("feature"), use_container_width=True)
        st.markdown("#### SHAP-style beeswarm")
        st.pyplot(beeswarm_figure(explain_rows), clear_figure=True)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 10 — RECOVERY OPTIMIZATION AGENT
# ══════════════════════════════════════════════════════════════════════════════
with tab10:
    st.markdown("## 🎯 Recovery Optimization Agent")
    opt_rows = [dict(row) for row in _db.get_all_transactions(200)]
    if opt_rows:
        opt_id = st.selectbox("Failure event", [row["payment_id"] for row in opt_rows], key="optimizer_txn")
        opt_event = next(row for row in opt_rows if row["payment_id"] == opt_id)
        rec = optimize_recovery(opt_event)
        st.json(rec)
        st.caption("The recommendation combines contextual Thompson Sampling for payment methods with Bayesian-style posterior search over retry timing windows.")
        if st.button("Run simulation", key="run_optimizer_simulation"):
            st.session_state.optimizer_sim = simulate_optimization(opt_rows, rounds=250)
        if st.session_state.get("optimizer_sim"):
            st.dataframe(pd.DataFrame(st.session_state.optimizer_sim["arms"]).T, use_container_width=True)
            st.metric("Simulated expected recovery rate", f"{st.session_state.optimizer_sim['recovery_rate']:.1%}")
            st.metric("Simulated recovered revenue", f"₹{st.session_state.optimizer_sim['revenue_recovered']:,.2f}")
    else:
        st.info("Waiting for payment failure events.")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 11 — EXPERIMENTATION AGENT
# ══════════════════════════════════════════════════════════════════════════════
with tab11:
    st.markdown("## 🧪 Experimentation Agent")
    st.caption("Online A/B and multi-armed-bandit strategy comparison.")
    ec1, ec2, ec3, ec4 = st.columns(4)
    strategy = ec1.selectbox("Strategy", ["retry_now", "retry_15m", "offer_upi", "offer_emi"])
    recovered = ec2.checkbox("Recovered", value=True)
    friction = ec3.number_input("Customer friction", min_value=0.0, max_value=10.0, value=1.0)
    time_to_recovery = ec4.number_input("Time to recovery (min)", min_value=0.0, value=15.0)
    revenue = st.number_input("Recovered revenue (₹)", min_value=0.0, value=1000.0)
    if st.button("Record experiment outcome"):
        record_experiment(strategy, recovered, revenue, friction, time_to_recovery)
        st.success("Outcome recorded.")
    report = experiment_report()
    if report["strategies"]:
        exp_df = pd.DataFrame(report["strategies"])
        st.dataframe(exp_df, use_container_width=True)
        st.bar_chart(exp_df.set_index("strategy")["recovery_rate"], use_container_width=True)
        st.caption("Confidence intervals use a 95% normal approximation; treat early samples as directional rather than conclusive.")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 12 — REVENUE ANOMALY DETECTION AGENT
# ══════════════════════════════════════════════════════════════════════════════
with tab12:
    st.markdown("## 🚨 Revenue Anomaly Detection Agent")
    anomaly_rows = [dict(row) for row in _db.get_all_transactions(1000)]
    anomaly_result = detect_anomalies(anomaly_rows)
    if anomaly_result["anomalies"]:
        st.error(f"Detected {len(anomaly_result['anomalies'])} payment-failure anomaly window(s).")
        st.dataframe(pd.DataFrame(anomaly_result["anomalies"]), use_container_width=True)
        alert_text = f"RecoverAI anomaly alert: {len(anomaly_result['anomalies'])} payment-failure anomaly window(s) detected."
        ac1, ac2 = st.columns(2)
        with ac1:
            if st.button("Send Slack alert", key="send_slack_alert"):
                from anomaly_detection import send_slack_alert
                st.success("Slack alert sent." if send_slack_alert(alert_text) else "Slack alert not sent; configure SLACK_WEBHOOK_URL.")
        with ac2:
            if st.button("Send email alert", key="send_email_alert"):
                from anomaly_detection import send_email_alert
                try:
                    sent = send_email_alert("RecoverAI revenue anomaly", alert_text)
                    st.success("Email alert sent." if sent else "Email alert not sent; configure SMTP_HOST and ALERT_EMAIL_TO.")
                except Exception as exc:
                    st.error(f"Email delivery failed: {exc}")
    else:
        st.success("No current anomaly detected.")
    st.markdown("#### Forecasted failure volume")
    forecast_df = forecast_anomalies(anomaly_rows)
    if not forecast_df.empty:
        st.line_chart(forecast_df.set_index("timestamp"), use_container_width=True)
    st.caption("Detection combines Isolation Forest when enough observations exist, rolling statistical thresholding, and Prophet when installed.")
