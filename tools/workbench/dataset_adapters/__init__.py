"""Adapters that translate between the agent and Logos' encrypted-volume
datasets. One adapter per `schema_family`; all share the same interface:

    query(reference: dict, dataset: Dataset, *, limit: int = 25) -> dict

Returns a normalized envelope: {dataset, dataset_type, title, results, note}.
"""
from __future__ import annotations

from typing import Protocol

from dataset_registry import Dataset


class Adapter(Protocol):
    def query(self, reference: dict, dataset: Dataset, *, limit: int = 25) -> dict: ...


def adapter_for(family: str) -> Adapter:
    """Return an adapter instance for the given schema_family.
    Falls back to the generic adapter for unknown families."""
    if family == "supplemental":
        from .supplemental import SupplementalAdapter
        return SupplementalAdapter()
    if family == "xref":
        from .xref import XrefAdapter
        return XrefAdapter()
    if family == "entity":
        from .entity import EntityAdapter
        return EntityAdapter()
    if family == "syntax":
        from .syntax import SyntaxAdapter
        return SyntaxAdapter()
    from .generic import GenericAdapter
    return GenericAdapter()


def envelope(dataset: Dataset, results: list, *, note: str = "") -> dict:
    """Build the uniform return envelope."""
    return {
        "dataset": dataset.dataset_id,
        "dataset_type": dataset.type,
        "title": dataset.title,
        "results": results,
        "note": note or (f"{len(results)} match(es)" if results else "no matches at this passage"),
    }
