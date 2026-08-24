"""
RecoverAI Enterprise — Streamlit Cloud entry point.

MUST be at repo root. Set "Main file path" = streamlit_app.py in Streamlit Cloud.

Patches sys.path so all flat imports inside recover_ai/ resolve correctly,
then delegates to recover_ai/app.py.
"""
import sys
import os

# ── Patch sys.path BEFORE any other import ───────────────────────────────────
_ROOT = os.path.dirname(os.path.abspath(__file__))
_PKG  = os.path.join(_ROOT, "recover_ai")

for _p in (_PKG, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── Set CWD to repo root so .env and relative paths resolve ──────────────────
os.chdir(_ROOT)

# ── Execute app.py in a namespace that inherits builtins + sys.path ──────────
# Passing a bare dict to exec() strips __builtins__, which breaks ALL imports
# inside app.py (including stdlib like os, sys, and third-party like plotly).
# We merge the current globals so builtins are available, then override
# __file__ / __name__ so app.py believes it is the main script.
_app_path = os.path.join(_PKG, "app.py")
_ns = dict(globals())          # inherit __builtins__ and all current bindings
_ns["__file__"] = _app_path
_ns["__name__"] = "__main__"
with open(_app_path, encoding="utf-8") as _f:
    exec(compile(_f.read(), _app_path, "exec"), _ns)  # noqa: S102
