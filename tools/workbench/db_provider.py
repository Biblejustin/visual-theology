"""Shared CompanionDB factory.

Extracted from app.py during the blueprint split (polish #5b) so
blueprints can resolve `get_db()` without importing from app — which
would create a circular import.

Tests still import `get_db` from `app` (and `app._db_instance = None`
to reset). app.py re-exports from this module, so existing test code
continues to work unchanged.

The cached instance honors the COMPANION_DB_PATH env var so tests can
swap to a temp file by:
    os.environ['COMPANION_DB_PATH'] = '/tmp/test.db'
    db_provider._db_instance = None    # force re-creation
"""

import os
from pathlib import Path

from companion_db import CompanionDB


TOOLS_DIR = Path(__file__).resolve().parent.parent

_db_instance = None


def get_db():
    """Return a cached CompanionDB instance, honoring COMPANION_DB_PATH."""
    global _db_instance
    if _db_instance is None:
        db_path = os.environ.get(
            "COMPANION_DB_PATH",
            str(TOOLS_DIR / "workbench" / "companion.db"),
        )
        _db_instance = CompanionDB(db_path)
    return _db_instance
