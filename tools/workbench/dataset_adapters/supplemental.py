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
    """Generic supplemental-data query. Mirrors dataset_tools.query_figurative_language:
    use the word cache to translate (book, chapter, verse_start, verse_end) into the
    STRING word refs that _find_word_ids expects (e.g. 'gnt/1234.5:6'), then look up
    supplemental rows by those word ids."""
    try:
        import dataset_tools
    except Exception:
        return []
    reader = dataset_tools._get_reader()
    if reader is None:
        return []
    try:
        cache = dataset_tools._get_word_cache()
        if cache is None:
            return []
        word_refs = cache.get_word_numbers(book, chapter, verse_start, verse_end) or []
        if not word_refs:
            return []
        word_ids = dataset_tools._find_word_ids(reader, filename,
                                                "SupplementalData.db", word_refs)
        if not word_ids:
            return []
        rows = dataset_tools._query_supplemental_by_word_ids(
            reader, filename, "SupplementalData.db", word_ids) or []
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
