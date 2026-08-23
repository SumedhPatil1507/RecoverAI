"""
RecoverAI Enterprise – Streamlit Merchant Intelligence Hub
• Plotly WebGL dashboards (funnel, dual-trace time-series, donut, histogram, bar)
• SHA-256 hash-chain ledger verification
• PII-safe: all data read from local SQLite; no raw webhook data displayed
• Compatible: Streamlit Cloud, local, Docker, AWS, Azure
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from decimal import Decimal

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

# ── Path resolution ───────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import database as db
from config import get_settings

settings = get_settings()
db.init_db()

# ── Page config (must be first Streamlit call) ────────────────────────────────
st.set_page_config(
    page_title="RecoverAI Enterprise",
    page_icon="🏦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Design tokens ─────────────────────────────────────────────────────────────
C = dict(
    blue   = "#4285f4",
    green  = "#34a853",
    orange = "#fbbc04",
    red    = "#ea4335",
    purple = "#ab47bc",
    teal   = "#26c6da",
    bg     = "#0d1b2a",
    surface= "#0a1628",
    border = "#1e3a5f",
    text   = "#c9d1d9",
)

STATUS_COLOURS = {
    "FAILED":            C["red"],
    "ML_SCORED":         C["blue"],
    "LOW_PRIORITY_SKIP": C["purple"],
    "AGENT_EVALUATED":   C["orange"],
    "ACTION_TRIGGERED":  C["teal"],
    "RECOVERING":        C["orange"],
    "RECOVERED":         C["green"],
    "EXPIRED":           C["purple"],
}

ROOT_CAUSE_COLOURS = {
    "GATEWAY_DOWN":       C["blue"],
    "USER_CANCELLED":     C["orange"],
    "INSUFFICIENT_FUNDS": C["red"],
    "NETWORK_TIMEOUT":    C["teal"],
    "INVALID_DETAILS":    C["purple"],
    "BANK_DECLINE":       "#e91e63",
    "UNKNOWN":            C["text"],
}

_PLOTLY = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(13,27,42,0.7)",
    font=dict(color=C["text"], family="Inter, sans-serif", size=12),
    margin=dict(l=16, r=16, t=38, b=16),
)

# ── CSS injection ─────────────────────────────────────────────────────────────
st.markdown(
    f"""
    <style>
    html, body, .stApp {{ background-color:{C['bg']} !important; }}
    section[data-testid="stSidebar"] {{ background-color:{C['surface']} !important; }}
    .kpi-card {{
        background:linear-gradient(135deg,{C['border']} 0%,{C['surface']} 100%);
        border:1px solid {C['border']}; border-radius:12px;
        padding:1.1rem 1.4rem; margin-bottom:.5rem; min-height:100px;
    }}
    .kpi-label {{ color:{C['teal']}; font-size:.72rem;
                  text-transform:uppercase; letter-spacing:.1em; }}
    .kpi-value {{ color:#fff; font-size:1.85rem; font-weight:700; margin:.15rem 0; }}
    .kpi-sub   {{ color:{C['text']}; font-size:.76rem; }}
    .badge-ok  {{
        background:#1a4731; color:{C['green']};
        border:1px solid {C['green']}; border-radius:8px;
        padding:.3rem .85rem; font-weight:700; display:inline-block; font-size:.9rem;
    }}
    .badge-err {{
        background:#4a1010; color:{C['red']};
        border:1px solid {C['red']}; border-radius:8px;
        padding:.3rem .85rem; font-weight:700; display:inline-block; font-size:.9rem;
    }}
    div[data-testid="metric-container"] {{ display:none; }}
    </style>
    """,
    unsafe_allow_html=True,
)

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🏦 RecoverAI Enterprise")
    st.caption(f"v{settings.app_version}  ·  {settings.environment.upper()}")
    st.divider()

    auto_refresh = st.toggle("⟳ Auto Refresh", value=True, key="ar")
    refresh_ms   = st.slider("Interval (s)", 2, 30,
                             settings.dashboard_refresh_seconds) * 1000

    st.divider()
    if st.button("🔄 Force Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.caption(f"**DB:** `{settings.database_path}`")
    st.caption(f"**Queue workers:** {settings.queue_workers}")
    st.caption(f"**ML skip threshold:** {settings.ml_low_priority_threshold}")
    st.caption(f"**Max discount guardrail:** {settings.max_discount_pct}%")

# ── Auto-refresh (streamlit-autorefresh) ─────────────────────────────────────
if auto_refresh:
    st_autorefresh(interval=refresh_ms, key="dashboard_refresh")

# ── Cached data loaders ───────────────────────────────────────────────────────

@st.cache_data(ttl=3)
def _load_summary() -> dict:
    m = db.get_summary_metrics()
    return {k: float(v) if isinstance(v, Decimal) else v for k, v in m.items()}


@st.cache_data(ttl=3)
def _load_funnel() -> dict:
    return db.get_funnel_counts()


@st.cache_data(ttl=3)
def _load_root_causes() -> list:
    return db.get_root_cause_breakdown()


@st.cache_data(ttl=3)
def _load_timeseries() -> pd.DataFrame:
    rows = db.get_timeseries_data()
    if not rows:
        return pd.DataFrame(columns=["minute","revenue_at_risk","revenue_recovered"])
    df = pd.DataFrame([{
        "minute":            r["minute"],
        "revenue_at_risk":   float(r["revenue_at_risk"]),
        "revenue_recovered": float(r["revenue_recovered"]),
    } for r in rows])
    df["minute"] = pd.to_datetime(df["minute"])
    return df.sort_values("minute").reset_index(drop=True)


@st.cache_data(ttl=3)
def _load_audit_logs() -> pd.DataFrame:
    rows = db.get_audit_logs(200)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([dict(r) for r in rows])


@st.cache_data(ttl=3)
def _load_transactions() -> pd.DataFrame:
    rows = db.get_all_transactions(300)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([dict(r) for r in rows])


# ── KPI helper ────────────────────────────────────────────────────────────────

def _kpi(col, label: str, value: str, sub: str, colour: str = "#ffffff") -> None:
    col.markdown(
        f'<div class="kpi-card">'
        f'<div class="kpi-label">{label}</div>'
        f'<div class="kpi-value" style="color:{colour};">{value}</div>'
        f'<div class="kpi-sub">{sub}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ═════════════════════════════════════════════════════════════════════════════
# PAGE CONTENT
# ═════════════════════════════════════════════════════════════════════════════

st.markdown("## 🏦 RecoverAI Enterprise — Intelligence Hub")
st.caption(
    f"Updated: {datetime.now().strftime('%H:%M:%S')}  ·  "
    "Razorpay AI Buildathon · Track 03"
)

# ── KPI Row ───────────────────────────────────────────────────────────────────
summary         = _load_summary()
total_risk      = summary.get("total_at_risk", 0.0)
total_recovered = summary.get("total_recovered", 0.0)
recovery_rate   = summary.get("recovery_rate", 0.0)
avg_score       = summary.get("avg_recoverability_score", 0.0)
net_loss        = max(0.0, total_risk - total_recovered)

try:
    _ledger_ok, _ledger_msg = db.verify_audit_integrity()
except Exception:
    _ledger_ok, _ledger_msg = False, "Verification failed"

c1, c2, c3, c4, c5 = st.columns(5)

_kpi(c1, "💰 Revenue at Risk",   f"₹{total_risk:,.0f}",      "All failed transactions")
_kpi(c2, "✅ Revenue Recovered", f"₹{total_recovered:,.0f}", "Successfully recovered", C["green"])
_kpi(
    c3, "📈 Recovery Rate", f"{recovery_rate:.1f}%", "Recovered / Total",
    C["green"] if recovery_rate >= 40 else C["orange"] if recovery_rate >= 20 else C["red"],
)
_kpi(c4, "🤖 Avg ML Score", f"{avg_score:.3f}", "Recoverability confidence", C["teal"])

badge = (
    '<span class="badge-ok">🔒 LEDGER VERIFIED</span>'
    if _ledger_ok else
    '<span class="badge-err">⚠️ ANOMALY DETECTED</span>'
)
c5.markdown(
    f'<div class="kpi-card">'
    f'<div class="kpi-label">🔐 Audit Ledger</div>'
    f'<div style="margin-top:.5rem">{badge}</div>'
    f'<div class="kpi-sub" style="margin-top:.4rem">SHA-256 hash chain</div>'
    f'</div>',
    unsafe_allow_html=True,
)

st.markdown("<br>", unsafe_allow_html=True)

# ── Row 1: Funnel + Donut ─────────────────────────────────────────────────────
col_f, col_d = st.columns([3, 2])

with col_f:
    st.markdown("#### 🔽 ML-Augmented Recovery Funnel")
    funnel = _load_funnel()
    stages = ["Ingested", "ML Scored", "Agent Evaluated", "Action Triggered", "Recovered"]
    values = [
        funnel.get("ingested", 0),
        funnel.get("ml_scored", 0),
        funnel.get("agent_evaluated", 0),
        funnel.get("action_triggered", 0),
        funnel.get("recovered", 0),
    ]
    fig_f = go.Figure(go.Funnel(
        y=stages, x=values,
        textposition="inside",
        textinfo="value+percent initial",
        marker=dict(
            color=[C["red"], C["blue"], C["orange"], C["teal"], C["green"]],
            line=dict(width=1.5, color=C["bg"]),
        ),
        connector=dict(line=dict(color=C["border"], dash="dot", width=2)),
    ))
    fig_f.update_layout(**_PLOTLY, height=320)
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
            textinfo="label+percent",
            textfont=dict(size=11),
            hovertemplate="<b>%{label}</b><br>Count: %{value}<br>%{percent}<extra></extra>",
        ))
        fig_d.update_layout(
            **_PLOTLY, height=320, showlegend=True,
            legend=dict(orientation="h", y=-0.18),
            annotations=[dict(
                text=f"<b>{sum(vals)}</b><br>Total",
                x=0.5, y=0.5,
                font=dict(size=13, color="#fff"),
                showarrow=False,
            )],
        )
        st.plotly_chart(fig_d, use_container_width=True)
    else:
        st.info("Waiting for data — start the simulator.", icon="⏳")

# ── Row 2: Dual-trace Time-Series ─────────────────────────────────────────────
st.markdown("#### 📊 Revenue at Risk vs. Recovered — Live Time-Series (WebGL)")
ts_df = _load_timeseries()

if not ts_df.empty:
    fig_ts = go.Figure()
    fig_ts.add_trace(go.Scatter(
        x=ts_df["minute"], y=ts_df["revenue_at_risk"],
        name="Revenue at Risk", mode="lines",
        line=dict(color=C["red"], width=2.5),
        fill="tozeroy", fillcolor="rgba(234,67,53,0.12)",
        hovertemplate="₹%{y:,.0f}<extra>At Risk</extra>",
    ))
    fig_ts.add_trace(go.Scatter(
        x=ts_df["minute"], y=ts_df["revenue_recovered"],
        name="Revenue Recovered", mode="lines",
        line=dict(color=C["green"], width=2.5),
        fill="tozeroy", fillcolor="rgba(52,168,83,0.18)",
        hovertemplate="₹%{y:,.0f}<extra>Recovered</extra>",
    ))
    fig_ts.update_layout(
        **_PLOTLY, height=380, hovermode="x unified",
        xaxis=dict(
            gridcolor=C["border"],
            rangeslider=dict(visible=True, bgcolor=C["surface"], thickness=0.07),
            rangeselector=dict(
                bgcolor=C["surface"], activecolor=C["border"],
                buttons=[
                    dict(count=5,  label="5m",  step="minute", stepmode="backward"),
                    dict(count=30, label="30m", step="minute", stepmode="backward"),
                    dict(count=1,  label="1h",  step="hour",   stepmode="backward"),
                    dict(step="all", label="All"),
                ],
            ),
        ),
        yaxis=dict(gridcolor=C["border"], tickprefix="₹", tickformat=",.0f"),
        legend=dict(orientation="h", y=1.06, bgcolor="rgba(0,0,0,0)"),
    )
    st.plotly_chart(fig_ts, use_container_width=True)
else:
    st.info(
        "No time-series data yet.  \n"
        "**Run:** `python recover_ai/data_simulator.py` to start generating events.",
        icon="📡",
    )

# ── ML Score Histogram ────────────────────────────────────────────────────────
txn_df = _load_transactions()
st.markdown("#### 🤖 ML Recoverability Score Distribution")

if not txn_df.empty and "recoverability_score" in txn_df.columns:
    scores = txn_df["recoverability_score"].dropna()
    scores = scores[scores > 0]
    if not scores.empty:
        fig_h = go.Figure(go.Histogram(
            x=scores, nbinsx=25,
            marker_color=C["blue"],
            marker_line=dict(color=C["bg"], width=0.8),
            hovertemplate="Score: %{x:.2f}<br>Count: %{y}<extra></extra>",
        ))
        fig_h.add_vline(
            x=settings.ml_low_priority_threshold,
            line_dash="dash", line_color=C["red"], line_width=2,
            annotation_text=f"Skip Threshold ({settings.ml_low_priority_threshold})",
            annotation_position="top right",
            annotation_font_color=C["red"],
        )
        fig_h.update_layout(
            **_PLOTLY, height=260,
            xaxis=dict(title="Recoverability Score", gridcolor=C["border"]),
            yaxis=dict(title="Transaction Count",    gridcolor=C["border"]),
            showlegend=False,
        )
        st.plotly_chart(fig_h, use_container_width=True)
    else:
        st.info("ML scoring in progress…", icon="⚙️")

# ── Transaction Status Bar ────────────────────────────────────────────────────
if not txn_df.empty:
    st.markdown("#### 📋 Transaction Status Distribution")
    sc = txn_df["status"].value_counts().reset_index()
    sc.columns = ["status", "count"]
    fig_b = go.Figure(go.Bar(
        x=sc["status"], y=sc["count"],
        marker_color=[STATUS_COLOURS.get(s, C["teal"]) for s in sc["status"]],
        text=sc["count"], textposition="outside",
        hovertemplate="<b>%{x}</b><br>%{y}<extra></extra>",
    ))
    fig_b.update_layout(
        **_PLOTLY, height=260,
        xaxis=dict(gridcolor=C["border"]),
        yaxis=dict(gridcolor=C["border"]),
        showlegend=False,
    )
    st.plotly_chart(fig_b, use_container_width=True)

# ── Ledger Verification Section ───────────────────────────────────────────────
st.markdown("---")
st.markdown("#### 🔐 Cryptographic Audit Ledger Verification")

if st.button("🔍 Verify Ledger Integrity Now", type="primary"):
    with st.spinner("Replaying entire SHA-256 hash chain…"):
        ok, msg = db.verify_audit_integrity()
    if ok:
        st.markdown(
            '<div class="badge-ok" style="font-size:1rem;padding:.5rem 1.2rem">'
            '🔒 100% IMMUTABLE &amp; VERIFIED</div>',
            unsafe_allow_html=True,
        )
        st.success(msg)
    else:
        st.markdown(
            '<div class="badge-err" style="font-size:1rem;padding:.5rem 1.2rem">'
            '⚠️ TAMPER DETECTED — Investigate Immediately</div>',
            unsafe_allow_html=True,
        )
        st.error(msg)

# ── Audit Trail ───────────────────────────────────────────────────────────────
with st.expander("📜 Real-Time Audit Trail  ·  Immutable SHA-256 Hash-Chain Log",
                 expanded=False):
    audit_df = _load_audit_logs()
    if not audit_df.empty:
        disp = [c for c in [
            "timestamp", "transaction_id", "action_taken", "source",
            "recoverability_score", "amount_paise",
            "decision_rationale", "current_hash",
        ] if c in audit_df.columns]
        adf = audit_df[disp].copy()

        if "amount_paise" in adf.columns:
            adf["amount"] = (adf["amount_paise"] / 100).map(lambda x: f"₹{x:,.2f}")
            adf = adf.drop(columns=["amount_paise"])

        if "current_hash" in adf.columns:
            adf["hash"] = adf["current_hash"].str[:16] + "…"
            adf = adf.drop(columns=["current_hash"])

        if "recoverability_score" in adf.columns:
            adf["recoverability_score"] = adf["recoverability_score"].map(
                lambda x: f"{x:.4f}"
            )

        st.dataframe(adf, use_container_width=True, height=360)
        st.caption(f"{len(audit_df)} audit records displayed")

        # Per-row JSON inspector
        with st.expander("🔎 Raw JSON Inspector — select a row"):
            idx = st.number_input(
                "Row index (0 = newest)", min_value=0,
                max_value=max(0, len(audit_df) - 1), value=0, step=1,
            )
            row = audit_df.iloc[int(idx)]
            st.json({
                "transaction_id":     row.get("transaction_id"),
                "action_taken":       row.get("action_taken"),
                "source":             row.get("source"),
                "recoverability_score": float(row.get("recoverability_score", 0)),
                "decision_rationale": row.get("decision_rationale"),
                "timestamp":          row.get("timestamp"),
                "previous_hash":      row.get("previous_hash"),
                "current_hash":       row.get("current_hash"),
            })
    else:
        st.info("No audit records yet. Send some events via the simulator.", icon="📝")

# ── Footer ────────────────────────────────────────────────────────────────────
st.divider()
st.caption(
    "RecoverAI Enterprise v2.0.0  ·  Razorpay AI Buildathon Track 03  ·  "
    "FastAPI · SQLite WAL · LightGBM · SHA-256 Ledger · Streamlit · Plotly WebGL"
)
