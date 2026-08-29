"""
RecoverAI Enterprise — Streamlit Cloud entry point (repo root).

Patches sys.path so recover_ai/ imports resolve, then imports app
as a module. Streamlit detects all st.* calls in imported modules.
"""
import sys
import os

_ROOT = os.path.dirname(os.path.abspath(__file__))
_PKG  = os.path.join(_ROOT, "recover_ai")

for _p in (_PKG, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.chdir(_ROOT)

# app.py patches its own sys.path at the top and calls set_page_config first.
# Importing it as a module is the most robust approach — no exec/runpy needed.
import recover_ai.app  # noqa: F401, E402
