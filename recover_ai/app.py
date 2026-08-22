"""
RecoverAI – Streamlit Merchant Hub
Interactive Plotly WebGL dashboards with real-time data from the SQLite database.
"""

from __future__ import annotations

import sys
import os
import time
import sqlite3
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# Allow importing sibling modules when run from the recover_ai/ directory
sys.path.insert(0, os.path.dirname(__file__))

import database as db
from config import get_settings

settings = get_settings()

# ── Page Config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="RecoverAI – Merchant Hub",
    page_icon="💳",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Ensure DB is ready (no-op if already initialised) ────────────────────────
db.init_db()

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown(
    """
    <style>
    .metric-card {
        background: linear-gradient(135deg, #1e3a5f 0%, #0f2440 100%);
        border-radius: 12px;
        padding: 1.2rem 1.5rem;
        border: 1px solid #2e5090;
    }
    .metric-label { color: #8ab4f8; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.1em; }
    .metric-value { color: #ffffff; font-size: 2rem; font-weight: 700; margin-top: 0.2rem; }
    .metric-delta { color: #34a853; font-size: 0.85rem; margin-top: 0.1rem; }
    .stApp { background-color: #0d1b2a; }
    section[data-testid="stSidebar"] { background-color: #0a1628; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.image(
        "https://razorpay.com/newsroom/wp-content/uploads/2022/06/Razorpay_logo_new.svg",
        width=140,
        use_column_width=False,
    )
    st.title("RecoverAI")
    st.caption("Agentic Payment Recovery Engine")
    st.divider()

    auto_refresh = st.toggle("⟳ Auto Refresh", value=True, key="auto_refresh")
    refresh_interval = st.slider(
        "Refresh interval (s)", min_value=2, max_value=30,
        value=settings.dashboard_refresh_seconds, step=1,
    )

    st.divider()
    st.caption(f"DB: `{settings.database_path}`")
    if st.button("🔄 Force Refresh"):
        st.rerun()

# ── Data Loaders ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=2)
def load_summary() -> dict:
    return db.get_summary_metrics()


@st.cache_data(ttl=2)
def load_funnel() -> dict:
    return db.get_funnel_counts()


@st.cache_data(ttl=2)
def load_root_causes() -> list[dict]:
    return db.get_root_cause_breakdown()


@st.cache_data(ttl=2)
def load_timeseries() -> pd.DataFrame:
    rows = db.get_timeseries_data()
    if not rows:
        return pd.DataFrame(columns=["minute", "revenue_at_risk", "revenue_recovered"])
    df = pd.DataFrame(rows)
    df["minute"] = pd.to_datetime(df["minute"])
    df = df.sort_values("minute").reset_index(drop=True)
    return df


@st.cache_data(ttl=2)
def load_audit_logs() -> pd.DataFrame:
    rows = db.get_audit_logs(limit=200)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([dict(r) for r in rows])


@st.cache_data(ttl=2)
def load_transactions() -> pd.DataFrame:
    rows = db.get_all_transactions(limit=300)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([dict(r) for r in rows])


# ── Colour Palette ────────────────────────────────────────────────────────────
BLUE   = "#4285f4"
GREEN  = "#34a853"
ORANGE = "#fbbc04"
RED    = "#ea4335"
PURPLE = "#ab47bc"
TEAL   = "#26c6da"

STATUS_COLOURS = {
    "FAILED":     RED,
    "RECOVERING": ORANGE,
    "RECOVERED":  GREEN,
    "EXPIRED":    PURPLE,
}
ROOT_CAUSE_COLOURS = {
    "Bank Downtime":      BLUE,
    "Customer Drop-off":  ORANGE,
    "Insufficient Funds": RED,
    "Unknown":            TEAL,
}

PLOTLY_DARK = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(13,27,42,0.6)",
    font=dict(color="#c9d1d9", family="Inter, sans-serif"),
    margin=dict(l=20, r=20, t=40, b=20),
)

# ── Page Header ───────────────────────────────────────────────────────────────
st.markdown("## 💳 RecoverAI — Merchant Recovery Hub")
st.caption(
    f"Last updated: {datetime.now().strftime('%H:%M:%S')}  •  "
    "Track 03 · Razorpay AI Buildathon"
)
st.divider()

# ── KPI Metric Row ────────────────────────────────────────────────────────────
summary = load_summary()

col1, col2, col3, col4 = st.columns(4)

with col1:
    st.markdown(
        f"""<div class="metric-card">
        <div class="metric-label">💰 Total Revenue at Risk</div>
        <div class="metric-value">₹{summary.get('total_at_risk', 0):,.0f}</div>
        <div class="metric-delta">All failed transactions</div>
        </div>""",
        unsafe_allow_html=True,
    )

with col2:
    st.markdown(
        f"""<div class="metric-card">
        <div class="metric-label">✅ Revenue Recovered</div>
        <div class="metric-value">₹{summary.get('total_recovered', 0):,.0f}</div>
        <div class="metric-delta">Successfully retried</div>
        </div>""",
        unsafe_allow_html=True,
    )

with col3:
    recovery_rate = summary.get("recovery_rate", 0)
    colour = GREEN if recovery_rate >= 40 else ORANGE if recovery_rate >= 20 else RED
    st.markdown(
        f"""<div class="metric-card">
        <div class="metric-label">📈 Recovery Rate</div>
        <div class="metric-value" style="color:{colour};">{recovery_rate:.1f}%</div>
        <div class="metric-delta">Recovered / Total failures</div>
        </div>""",
        unsafe_allow_html=True,
    )

with col4:
    net_loss = max(0, summary.get("total_at_risk", 0) - summary.get("total_recovered", 0))
    st.markdown(
        f"""<div class="metric-card">
        <div class="metric-label">⚠️ Net Revenue Loss</div>
        <div class="metric-value" style="color:{RED};">₹{net_loss:,.0f}</div>
        <div class="metric-delta">Unrecovered amount</div>
        </div>""",
        unsafe_allow_html=True,
    )

st.markdown("<br>", unsafe_allow_html=True)

# ── Chart Row 1: Conversion Funnel + Donut ────────────────────────────────────
col_funnel, col_donut = st.columns([3, 2])

with col_funnel:
    st.markdown("#### 🔽 Recovery Conversion Funnel")
    funnel = load_funnel()

    stages   = ["Failures Ingested", "Classified", "Action Triggered", "Recovered"]
    values   = [
        funnel.get("ingested", 0),
        funnel.get("classified", 0),
        funnel.get("action_triggered", 0),
        funnel.get("recovered", 0),
    ]
    colours  = [RED, ORANGE, BLUE, GREEN]

    fig_funnel = go.Figure(
        go.Funnel(
            y=stages,
            x=values,
            textposition="inside",
            textinfo="value+percent initial",
            marker=dict(color=colours, line=dict(width=1.5, color="#0d1b2a")),
            connector=dict(line=dict(color="#2e5090", dash="dot", width=2)),
        )
    )
    fig_funnel.update_layout(
        **PLOTLY_DARK,
        height=320,
        yaxis=dict(gridcolor="rgba(46,80,144,0.3)"),
    )
    st.plotly_chart(fig_funnel, use_container_width=True)

with col_donut:
    st.markdown("#### 🍩 Failure Root Cause Breakdown")
    rc_data = load_root_causes()

    if rc_data:
        labels  = [r["root_cause"] for r in rc_data]
        vals    = [r["count"]      for r in rc_data]
        clrs    = [ROOT_CAUSE_COLOURS.get(l, TEAL) for l in labels]

        fig_donut = go.Figure(
            go.Pie(
                labels=labels,
                values=vals,
                hole=0.55,
                marker=dict(colors=clrs, line=dict(color="#0d1b2a", width=2)),
                textinfo="label+percent",
                textfont=dict(size=12),
                hovertemplate="<b>%{label}</b><br>Count: %{value}<br>Share: %{percent}<extra></extra>",
            )
        )
        fig_donut.update_layout(
            **PLOTLY_DARK,
            height=320,
            showlegend=True,
            legend=dict(orientation="h", y=-0.1),
            annotations=[
                dict(
                    text=f"<b>{sum(vals)}</b><br>Total",
                    x=0.5, y=0.5,
                    font=dict(size=14, color="#ffffff"),
                    showarrow=False,
                )
            ],
        )
        st.plotly_chart(fig_donut, use_container_width=True)
    else:
        st.info("Waiting for data…", icon="⏳")

# ── Chart Row 2: Dual-Trace Time-Series Area ──────────────────────────────────
st.markdown("#### 📊 Revenue at Risk vs. Revenue Recovered (Live Time-Series)")
ts_df = load_timeseries()

if not ts_df.empty:
    fig_ts = go.Figure()

    # Revenue at Risk – red fill
    fig_ts.add_trace(
        go.Scatter(
            x=ts_df["minute"],
            y=ts_df["revenue_at_risk"],
            name="Revenue at Risk",
            mode="lines",
            line=dict(color=RED, width=2),
            fill="tozeroy",
            fillcolor="rgba(234,67,53,0.15)",
            hovertemplate="₹%{y:,.0f}<extra>Revenue at Risk</extra>",
        )
    )

    # Revenue Recovered – green fill
    fig_ts.add_trace(
        go.Scatter(
            x=ts_df["minute"],
            y=ts_df["revenue_recovered"],
            name="Revenue Recovered",
            mode="lines",
            line=dict(color=GREEN, width=2),
            fill="tozeroy",
            fillcolor="rgba(52,168,83,0.20)",
            hovertemplate="₹%{y:,.0f}<extra>Revenue Recovered</extra>",
        )
    )

    fig_ts.update_layout(
        **PLOTLY_DARK,
        height=380,
        hovermode="x unified",
        xaxis=dict(
            gridcolor="rgba(46,80,144,0.3)",
            rangeslider=dict(visible=True, bgcolor="#0a1628", thickness=0.08),
            rangeselector=dict(
                bgcolor="#0a1628",
                activecolor="#1e3a5f",
                buttons=[
                    dict(count=5,  label="5m",  step="minute", stepmode="backward"),
                    dict(count=30, label="30m", step="minute", stepmode="backward"),
                    dict(count=1,  label="1h",  step="hour",   stepmode="backward"),
                    dict(step="all", label="All"),
                ],
            ),
        ),
        yaxis=dict(
            gridcolor="rgba(46,80,144,0.3)",
            tickprefix="₹",
            tickformat=",.0f",
        ),
        legend=dict(
            orientation="h", y=1.05, x=0,
            bgcolor="rgba(0,0,0,0)",
        ),
    )
    st.plotly_chart(fig_ts, use_container_width=True)
else:
    st.info(
        "No time-series data yet. Start the simulator: `python data_simulator.py`",
        icon="📡",
    )

# ── Transaction Status Breakdown ──────────────────────────────────────────────
st.markdown("#### 📋 Live Transaction Status Distribution")
txn_df = load_transactions()

if not txn_df.empty:
    status_counts = txn_df["status"].value_counts().reset_index()
    status_counts.columns = ["status", "count"]

    fig_bar = go.Figure(
        go.Bar(
            x=status_counts["status"],
            y=status_counts["count"],
            marker_color=[STATUS_COLOURS.get(s, TEAL) for s in status_counts["status"]],
            text=status_counts["count"],
            textposition="outside",
            hovertemplate="<b>%{x}</b><br>Count: %{y}<extra></extra>",
        )
    )
    fig_bar.update_layout(
        **PLOTLY_DARK,
        height=260,
        xaxis=dict(gridcolor="rgba(46,80,144,0.3)"),
        yaxis=dict(gridcolor="rgba(46,80,144,0.3)"),
        showlegend=False,
    )
    st.plotly_chart(fig_bar, use_container_width=True)

# ── Agent Audit Trail ─────────────────────────────────────────────────────────
with st.expander("🔍 Agent Audit Trail (Compliance Log)", expanded=False):
    audit_df = load_audit_logs()
    if not audit_df.empty:
        display_cols = [c for c in
            ["created_at", "transaction_id", "action", "source", "amount", "root_cause", "reasoning"]
            if c in audit_df.columns]
        audit_display = audit_df[display_cols].copy()

        if "amount" in audit_display.columns:
            audit_display["amount"] = audit_display["amount"].apply(
                lambda x: f"₹{x:,.2f}" if pd.notna(x) else "—"
            )

        st.dataframe(
            audit_display,
            use_container_width=True,
            height=350,
            column_config={
                "transaction_id": st.column_config.TextColumn("Txn ID", width="medium"),
                "action":         st.column_config.TextColumn("Action", width="medium"),
                "source":         st.column_config.TextColumn("Source", width="small"),
                "amount":         st.column_config.TextColumn("Amount", width="small"),
                "root_cause":     st.column_config.TextColumn("Root Cause", width="medium"),
                "reasoning":      st.column_config.TextColumn("AI Reasoning", width="large"),
                "created_at":     st.column_config.TextColumn("Timestamp", width="medium"),
            },
        )
        st.caption(f"Showing {len(audit_df)} most recent audit entries")
    else:
        st.info("No audit logs yet.", icon="📝")

# ── Footer ────────────────────────────────────────────────────────────────────
st.divider()
st.caption(
    "RecoverAI v1.0.0 · Razorpay AI Buildathon Track 03 · "
    "Built with FastAPI · SQLite WAL · OpenAI · Streamlit · Plotly"
)

# ── Auto-Refresh ──────────────────────────────────────────────────────────────
if auto_refresh:
    time.sleep(refresh_interval)
    st.rerun()
