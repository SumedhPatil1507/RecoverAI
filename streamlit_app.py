"""
RecoverAI Enterprise — Streamlit Cloud entry point.

This file MUST live at the repo root.
Streamlit Cloud points here: Main file path = streamlit_app.py

It adds recover_ai/ to sys.path so all flat imports (import database,
import config, etc.) resolve correctly, then imports the dashboard module.
"""
import sys
import os

# ── Resolve recover_ai package path ──────────────────────────────────────────
_ROOT = os.path.dirname(os.path.abspath(__file__))
_PKG  = os.path.join(_ROOT, "recover_ai")

for _p in (_PKG, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── Bootstrap DB (no-op if already initialised) ───────────────────────────────
import database as db
db.init_db()

# ── Run the dashboard ─────────────────────────────────────────────────────────
# Import app as a module — cleaner than exec(), works on all platforms
import importlib.util, types

_spec = importlib.util.spec_from_file_location(
    "recover_ai_app",
    os.path.join(_PKG, "app.py"),
)
_mod = importlib.util.module_from_spec(_spec)          # type: ignore[arg-type]
_mod.__file__ = os.path.join(_PKG, "app.py")
sys.modules["recover_ai_app"] = _mod
_spec.loader.exec_module(_mod)                         # type: ignore[union-attr]
