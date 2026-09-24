"""
RecoverAI test configuration.

Each test module gets its own isolated SQLite database so tampering in one
module does not contaminate another.  The DATABASE_PATH env var is set at
module collection time; conftest.py ensures the settings lru_cache and the
thread-local connection are flushed between test runs.
"""
from __future__ import annotations

import os
import tempfile

import pytest


def _reset_db(tmp_path_suffix: str) -> str:
    """Create a fresh temp DB, reset the settings cache and thread-local conn."""
    db = tempfile.mktemp(suffix=tmp_path_suffix)
    os.environ["DATABASE_PATH"] = db
    try:
        from config import get_settings
        get_settings.cache_clear()
    except Exception:
        pass
    try:
        import database as _db
        if hasattr(_db._local, "conn"):
            try:
                _db._local.conn.close()
            except Exception:
                pass
            del _db._local.conn
        _db.settings = get_settings() if (s := __import__("config")).get_settings else _db.settings  # noqa: F841
    except Exception:
        pass
    return db


# ── Per-session fixture: each test FILE gets its own DB ────────────────────────

@pytest.fixture(scope="session", autouse=True)
def _session_db_isolation(request: pytest.FixtureRequest) -> None:
    """No-op — individual modules set their own DATABASE_PATH at import time."""
    pass


# ── Hook: reset state before each test module is collected ────────────────────

def pytest_runtest_setup(item: pytest.Item) -> None:
    """
    Before each test runs, ensure DATABASE_PATH and RAZORPAY_WEBHOOK_SECRET
    match the module's own isolated values.  This prevents cross-module
    contamination when the test runner collects all modules in a single process.
    """
    module = item.module
    _reset = False

    # Each test module stores its expected DB path in a module-level variable.
    for db_attr in ("_TMP_DB", "_MODULE_DB"):
        db_path = getattr(module, db_attr, None)
        if db_path and os.environ.get("DATABASE_PATH") != db_path:
            os.environ["DATABASE_PATH"] = db_path
            _reset = True
            break

    # Also restore the webhook secret that each module expects
    for sec_attr in ("_TEST_SECRET", "_MODULE_SECRET"):
        secret = getattr(module, sec_attr, None)
        if secret and os.environ.get("RAZORPAY_WEBHOOK_SECRET") != secret:
            os.environ["RAZORPAY_WEBHOOK_SECRET"] = secret
            os.environ["AUDIT_HMAC_KEY"] = secret
            _reset = True
            break

    if _reset:
        try:
            from config import get_settings
            get_settings.cache_clear()
        except Exception:
            pass
        try:
            import database as _db
            if hasattr(_db._local, "conn"):
                try:
                    _db._local.conn.close()
                except Exception:
                    pass
                del _db._local.conn
            from config import get_settings as _gs
            _db.settings = _gs()
        except Exception:
            pass
