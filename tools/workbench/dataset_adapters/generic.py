"""Best-effort adapter for long-tail dataset families (clauses, grammar_ds,
ontology, thematic, misc). Introspects tables via dataset_tools.query_dataset_tables
and samples rows so every registered dataset is reachable from day one.
Replace with bespoke per-family adapters in follow-on slices as needs become clear."""
from __future__ import annotations

import logging
import re

import dataset_tools  # attribute access so monkeypatch works in tests

from dataset_registry import Dataset
from . import envelope

logger = logging.getLogger(__name__)

_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _discover_tables(filename: str) -> list[str]:
    try:
        reader = dataset_tools._get_reader()
        if reader is None:
            return []
        # Most .lbs* volumes contain an inner DB whose name matches the
        # filename stem; query_dataset_tables abstracts the lookup.
        return list(dataset_tools.query_dataset_tables(filename, None) or [])
    except Exception:
        logger.warning("table discovery failed for %s", filename, exc_info=True)
        return []


def _sample_rows(filename: str, table: str, reference: dict, limit: int) -> list[dict]:
    """Best-effort: pull up to `limit` rows from `table`, tagging each with
    its source table. We don't try to filter by passage here — many of these
    datasets aren't passage-keyed in an obvious column, so we let the agent
    inspect what came back.

    `table` is interpolated into SQL (sqlite3 doesn't bind table identifiers),
    so we whitelist it to a standard SQL identifier first. Today the source
    is trusted DB metadata; this guards future call paths that may surface
    agent-influenced names through the registry."""
    if not _SAFE_IDENT.match(table or ""):
        logger.warning("generic adapter: rejecting unsafe table name %r for %s",
                       table, filename)
        return []
    try:
        reader = dataset_tools._get_reader()
        if reader is None:
            return []
        sql = f"SELECT * FROM {table} LIMIT {int(limit)}"
        rows = reader.query_dataset(filename, None, sql) or []
        out = []
        for r in rows:
            d = dict(r) if not isinstance(r, dict) else r
            d["_table"] = table
            out.append(d)
        return out
    except Exception:
        logger.warning("row sample failed for %s/%s", filename, table, exc_info=True)
        return []


class GenericAdapter:
    def query(self, reference: dict, dataset: Dataset, *, limit: int = 25) -> dict:
        tables = _discover_tables(dataset.filename)
        if not tables:
            return envelope(dataset, [], note="dataset unreachable or has no inspectable tables")
        per_table_cap = max(1, limit // max(1, len(tables)))
        results: list[dict] = []
        for t in tables:
            results.extend(_sample_rows(dataset.filename, t, reference, per_table_cap))
            if len(results) >= limit:
                break
        return envelope(dataset, results[:limit])
