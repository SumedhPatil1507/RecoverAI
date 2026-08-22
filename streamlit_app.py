"""
Streamlit Cloud entry point.
Streamlit Cloud expects the app file at the REPO ROOT.
This thin shim adds recover_ai/ to sys.path then delegates to app.py.
"""
import sys
import os

# Make `import database`, `import config`, etc. resolve correctly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "recover_ai"))

# Re-execute the actual dashboard module
exec(
    open(os.path.join(os.path.dirname(__file__), "recover_ai", "app.py")).read(),
    {"__file__": os.path.join(os.path.dirname(__file__), "recover_ai", "app.py")},
)
