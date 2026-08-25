"""
RecoverAI Enterprise — Streamlit Cloud entry point.
Main file: streamlit_app.py (repo root)

Adds recover_ai/ to sys.path then runs app.py as __main__.
app.py is also directly runnable: streamlit run recover_ai/app.py
"""
import sys, os

_ROOT = os.path.dirname(os.path.abspath(__file__))
_PKG  = os.path.join(_ROOT, "recover_ai")
for _p in (_PKG, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.chdir(_ROOT)

import runpy
runpy.run_path(os.path.join(_PKG, "app.py"), run_name="__main__")
