"""Smoke tests for the Streamlit demo entrypoint."""

from pathlib import Path

from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[1]


def test_streamlit_dashboard_starts_without_script_errors() -> None:
    """Render the real app once to catch import and initialization regressions."""
    app = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=90).run()

    assert not app.exception, "Streamlit app raised an exception during startup"
    assert len(app.tabs) == 8
    assert app.title or app.header or app.subheader or app.markdown
