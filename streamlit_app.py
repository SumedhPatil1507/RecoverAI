"""
RecoverAI Enterprise — Streamlit Cloud entry point.

This file must stay at the repo root.
It only patches sys.path so recover_ai/ imports resolve, then imports app.py
as a proper Python module — no exec(), no runpy, no namespace tricks.
Streamlit detects all st.* calls regardless of which module they come from.
"""
import sys
import os

# ── Patch sys.path before anything else ──────────────────────────────────────
_ROOT = os.path.dirname(os.path.abspath(__file__))
_PKG  = os.path.join(_ROOT, "recover_ai")

for _p in (_PKG, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.chdir(_ROOT)

# ── Import app as a module — Streamlit sees all st.* calls automatically ─────
import importlib.util as _ilu

_spec = _ilu.spec_from_file_location("recover_ai.app", os.path.join(_PKG, "app.py"))
_mod  = _ilu.module_from_spec(_spec)

# Make sure the module itself can resolve its own relative imports
sys.modules.setdefault("recover_ai.app", _mod)
_spec.loader.exec_module(_mod)
