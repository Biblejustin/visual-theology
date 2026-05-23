"""Adapter for entity families (.lbsbpd people, .lbsplc places, .lbsthg things,
.lbsevt events, .lbsbrd referents, .lbsbio biographies, .lbsmps maps, .lbsspd
people-graphs). Wraps dataset_tools._query_entity_db, picking the right inner
DB name from the file extension."""
from __future__ import annotations

import logging
import os

import dataset_tools  # attribute access so monkeypatch works in tests

from dataset_registry import Dataset
from . import envelope

logger = logging.getLogger(__name__)


# Map known entity extensions to their canonical inner DB name.
_DB_NAME_BY_EXT = {
    ".lbsbpd": "biblicalpeople.db",
    ".lbsplc": "biblicalplaces.db",
    ".lbsthg": "biblicalthings.db",
    ".lbsevt": "biblicalevents.db",
    ".lbsbrd": "biblicalreferents.db",
    ".lbsbio": "biographies.db",
    ".lbsmps": "biblicalplacesmaps.db",
    ".lbsspd": "staticpeoplegraphs.db",
}


def _query_entity_for_passage(filename: str, db_name: str, book: int,
                              chapter: int, *_a, **_k) -> list[dict]:
    try:
        rows = dataset_tools._query_entity_db(filename, db_name, book, chapter) or []
    except Exception:
        logger.warning("entity query failed for %s/%s", filename, db_name, exc_info=True)
        return []
    return [dict(r) if not isinstance(r, dict) else r for r in rows]


class EntityAdapter:
    def query(self, reference: dict, dataset: Dataset, *, limit: int = 25) -> dict:
        ext = os.path.splitext(dataset.filename)[1].lower()
        db_name = _DB_NAME_BY_EXT.get(ext, "")
        if not db_name:
            return envelope(dataset, [], note=f"no db mapping for extension {ext}")
        rows = _query_entity_for_passage(
            dataset.filename, db_name,
            int(reference.get("book") or 0),
            int(reference.get("chapter") or 0),
        )
        return envelope(dataset, rows[:limit])
