"""
Internal path resolver.
Ensures recover_ai/ is always on sys.path regardless of how the package
is invoked (uvicorn recover_ai.main, python main.py, streamlit run app.py).
"""
import os, sys

_pkg = os.path.dirname(os.path.abspath(__file__))
if _pkg not in sys.path:
    sys.path.insert(0, _pkg)
