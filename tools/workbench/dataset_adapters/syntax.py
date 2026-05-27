"""Adapter for the .lbsclv / .lbssyn syntax-graph family (Cascadia Syntax
Graphs of the NT/SBL/LXX, Andersen-Forbes phrase markers).

Returns a *digested* phrase summary keyed to the passage — head term +
dependents + relation type — not raw graph node dumps. The inner-DB
schemas of these volumes are complex graph encodings; the B-1 stub
returns [] with an explicit diagnostic note. A richer digest is queued
for B-2.

SCHEMA NOTES (live introspection 2026-05-23):
    Files probed via dataset_tools.query_dataset_tables / direct
    reader.query_dataset against sqlite_master:
        CSGNT.lbssyn               -> [] (no tables returned)
        CSGNTSBL.lbssyn            -> [] (no tables returned)
        CSGLXXDA.lbssyn            -> [] (no tables returned)
        AndersenForbesHebrewOT.lbssyn -> [] (no tables returned)
        CSGNT.lbsclv               -> [] (no tables returned)

    The underlying files exist on disk (CSGNT.lbssyn is ~35 MB,
    CSGNT.lbsclv ~25 MB, etc.) and Logos itself opens them; however the
    encrypted-volume reader in this session yields no tables for the
    `.lbssyn` / `.lbsclv` family — even sibling `.lbssd` files (known-
    working in production) returned empty in the same probe, suggesting
    the empty result is a session/reader concern rather than a missing
    schema. Resolving that is out of scope for B-1.

    Status: opaque from the Python boundary. No obvious passage-keyed
    entry table we can dump in a few lines. Real digest design is queued
    for B-2 (likely needs joining a node/phrase table with a Bible-ref
    index once the reader path is unblocked, or a separate C# entrypoint
    that walks the graph natively).

    Choice: ship the B-1 stub per the plan's explicit allowance. Return
    an honest diagnostic rather than inventing a digest format we can't
    verify against real data.
"""
from __future__ import annotations

import logging

import dataset_tools  # attribute access so monkeypatch works in tests

from dataset_registry import Dataset
from . import envelope

logger = logging.getLogger(__name__)


def _digest_phrases(filename: str, book: int, chapter: int,
                    verse_start: int | None, verse_end: int | None,
                    limit: int) -> list[dict]:
    """B-1 stub. See module SCHEMA NOTES for why. Always returns [] in
    production."""
    try:
        reader = dataset_tools._get_reader()
        if reader is None:
            return []
    except Exception:
        logger.warning("syntax _get_reader failed for %s", filename, exc_info=True)
        return []
    # B-2: implement the real digest here once the schema is mapped.
    return []


class SyntaxAdapter:
    def query(self, reference: dict, dataset: Dataset, *, limit: int = 25) -> dict:
        rows = _digest_phrases(
            dataset.filename,
            int(reference.get("book") or 0),
            int(reference.get("chapter") or 0),
            reference.get("verse_start"),
            reference.get("verse_end"),
            limit,
        )
        return envelope(
            dataset,
            rows,
            note="syntax dataset is detected, but phrase-graph digest is not implemented yet",
        )
