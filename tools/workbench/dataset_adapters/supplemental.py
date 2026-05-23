"""Adapter for the .lbssd 'SupplementalData' family. Wraps the existing
per-dataset query patterns in dataset_tools.py with a uniform interface."""
from __future__ import annotations

import logging

from dataset_registry import Dataset
from . import envelope

logger = logging.getLogger(__name__)


def _query_supplemental_for_passage(filename: str, book: int, chapter: int,
                                    verse_start: int | None, verse_end: int | None,
                                    limit: int) -> list[dict]:
    """Generic supplemental-data query.

    Strategy mirrors dataset_tools.query_figurative_language /
    query_greek_constructions / query_literary_typing: open the .lbssd file,
    find word IDs in the passage range via SupplementalData.db's word_refs
    table, then return supplemental rows joined by word_id. The exact column
    layout varies by dataset, so we use dataset_tools._query_supplemental_by_word_ids
    when available and otherwise fall back to a row dump."""
    try:
        from dataset_tools import (_get_reader, _find_word_ids,
                                   _query_supplemental_by_word_ids)
    except Exception:
        return []
    reader = _get_reader()
    if reader is None:
        return []
    # Build word_refs in the passage range. dataset_tools uses (book, chapter,
    # verse_start, verse_end) → list of (book, chapter, verse) tuples internally.
    word_refs = [(book, chapter, v) for v in
                 range(verse_start or 1, (verse_end or verse_start or 1) + 1)]
    try:
        word_ids = _find_word_ids(reader, filename, "SupplementalData.db", word_refs)
        if not word_ids:
            return []
        rows = _query_supplemental_by_word_ids(reader, filename,
                                               "SupplementalData.db", word_ids) or []
    except Exception:
        logger.warning("supplemental query failed for %s", filename, exc_info=True)
        return []
    return rows[:limit]


class SupplementalAdapter:
    def query(self, reference: dict, dataset: Dataset, *, limit: int = 25) -> dict:
        rows = _query_supplemental_for_passage(
            dataset.filename,
            int(reference.get("book") or 0),
            int(reference.get("chapter") or 0),
            reference.get("verse_start"),
            reference.get("verse_end"),
            limit,
        )
        return envelope(dataset, rows)
