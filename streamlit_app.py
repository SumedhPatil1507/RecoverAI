"""
Streamlit Cloud entry-point (repo root).
Adds recover_ai/ to sys.path then runs app.py cleanly.
"""
import sys
import os

_pkg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recover_ai")
if _pkg not in sys.path:
    sys.path.insert(0, _pkg)

# Run the actual dashboard
exec(                                                        # noqa: S102
    open(os.path.join(_pkg, "app.py")).read(),
    {"__file__": os.path.join(_pkg, "app.py")},
)
