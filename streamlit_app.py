"""
RecoverAI Enterprise — Streamlit Cloud entry point.

MUST be at repo root. Set "Main file path" = streamlit_app.py in Streamlit Cloud.

Patches sys.path so all flat imports inside recover_ai/ resolve correctly,
then delegates to recover_ai/app.py via exec (so Streamlit sees it as one
continuous script and set_page_config is only called once).
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

# ── Execute app.py directly — exec preserves the patched sys.path ────────────
# runpy.run_path() creates an isolated namespace that loses the path patch;
# exec() shares the current globals so imports inside app.py work correctly.
_app_path = os.path.join(_PKG, "app.py")
with open(_app_path, encoding="utf-8") as _f:
    exec(compile(_f.read(), _app_path, "exec"), {"__file__": _app_path, "__name__": "__main__"})  # noqa: S102
