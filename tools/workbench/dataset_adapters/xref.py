"""Adapter for the .lbsxrf cross-reference family. Wraps the existing
query_theology_xrefs / query_curated_cross_refs machinery in dataset_tools."""
from __future__ import annotations

import logging

import dataset_tools  # attribute access so monkeypatch works in tests

from dataset_registry import Dataset
from . import envelope

logger = logging.getLogger(__name__)


def _query_xref_for_passage(filename: str, book: int, chapter: int,
                            verse_start: int | None, verse_end: int | None,
                            limit: int) -> list[dict]:
    """Generic cross-reference query against a .lbsxrf volume.

    Mirrors dataset_tools.query_theology_xrefs: open the file via the
    encrypted-volume reader, select CrossReferences rows whose ref column
    matches the passage range hex clause. Returns plain dicts; if the
    reader is unavailable, returns []."""
    try:
        reader = dataset_tools._get_reader()
    except Exception:
        return []
    if reader is None:
        return []
    try:
        # _ref_hex_clause_for_col already calls _protestant_to_logos internally,
        # matching query_theology_xrefs (dataset_tools.py:264).
        clause = dataset_tools._ref_hex_clause_for_col("dtr.Reference", book, chapter)
        sql = (
            f"SELECT cr.ArticleName, cr.ArticleTitle, cr.ResourceId, "
            f"ge.Label as Tradition, te.Label as Topic "
            f"FROM CrossReferences cr "
            f"JOIN DataTypeReferences dtr ON cr.CanonReferenceId = dtr.DataTypeReferenceId "
            f"LEFT JOIN GroupExpressions ge ON cr.GroupId = ge.GroupId AND ge.Language = 'en' "
            f"LEFT JOIN TypeExpressions te ON cr.TypeId = te.TypeId AND te.Language = 'en' "
            f"WHERE {clause} LIMIT {int(limit)}"
        )
        rows = reader.query_dataset(filename, "CrossReferences.db", sql) or []
    except Exception:
        logger.warning("xref query failed for %s", filename, exc_info=True)
        return []
    return [dict(r) if not isinstance(r, dict) else r for r in rows[:limit]]


class XrefAdapter:
    def query(self, reference: dict, dataset: Dataset, *, limit: int = 25) -> dict:
        rows = _query_xref_for_passage(
            dataset.filename,
            int(reference.get("book") or 0),
            int(reference.get("chapter") or 0),
            reference.get("verse_start"),
            reference.get("verse_end"),
            limit,
        )
        return envelope(dataset, rows)
