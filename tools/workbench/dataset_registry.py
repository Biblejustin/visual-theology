"""Registry of Logos research datasets present in the library.

Auto-discovered by joining the catalog + ResourceManager DBs. Each dataset
is classified into a `schema_family` from its file extension so adapters can
key off the family rather than per-dataset bespoke code.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass

# Default paths (live library on this machine). Tests pass explicit paths.
_DEFAULT_CATALOG = "/Volumes/External/Logos4/Data/e3txalek.5iq/LibraryCatalog/catalog.db"
_DEFAULT_RM = "/Volumes/External/Logos4/Data/e3txalek.5iq/ResourceManager/ResourceManager.db"

# Lbx types whose entries are NOT research datasets and should be filtered out.
DENYLIST_TYPES = frozenset({
    "lbx.reverse-interlinear",
    "lbx.interactive",
    "lbx.pronunciations",
})

# Extension → schema_family. Anything unknown becomes 'misc'.
_FAMILY_BY_EXT = {
    ".lbssd":  "supplemental",
    ".lbsxrf": "xref",
    ".lbsbpd": "entity", ".lbsplc": "entity", ".lbsthg": "entity",
    ".lbsevt": "entity", ".lbsbrd": "entity", ".lbsbio": "entity",
    ".lbsmps": "entity", ".lbsspd": "entity",
    ".lbsclv": "syntax", ".lbssyn": "syntax",
    ".lbscls": "clauses",
    ".lbsgrm": "grammar_ds",
    ".lbslco": "ontology", ".lbslsto": "ontology", ".lbsch": "ontology",
    ".lbslcv": "ontology", ".lbswsn": "ontology", ".lbsuav": "ontology",
    ".lbsptd": "thematic", ".lbstod": "thematic", ".lbsanc": "thematic",
    ".lbsct":  "thematic", ".lbspsd": "thematic", ".lbslms": "thematic",
    ".lbspcd": "thematic", ".lbsnt":  "thematic", ".lbsfbm": "thematic",
    ".lbsexc": "thematic", ".lbsiw":  "thematic", ".lbslcr": "thematic",
}

VALID_FAMILIES = frozenset({"supplemental", "xref", "entity", "syntax",
                            "clauses", "grammar_ds", "ontology", "thematic", "misc"})


@dataclass(frozen=True)
class Dataset:
    dataset_id: str   # e.g. "LLS:BULLINGER"
    title: str
    type: str         # e.g. "lbx.supplementaldata"
    filename: str     # basename, e.g. "BULLINGER-FIGURES.lbssd"
    location: str     # full path from catalog
    schema_family: str


def _ext(filename: str) -> str:
    """Return the lower-cased extension including the dot."""
    return os.path.splitext(filename)[1].lower()


def _classify(filename: str) -> str:
    return _FAMILY_BY_EXT.get(_ext(filename), "misc")


class DatasetRegistry:
    def __init__(self, catalog_path: str = _DEFAULT_CATALOG,
                 resource_manager_path: str = _DEFAULT_RM):
        self._catalog_path = catalog_path
        self._rm_path = resource_manager_path
        self._datasets: list[Dataset] = []
        self._by_id: dict[str, Dataset] = {}

    def load(self) -> None:
        """Populate the registry by joining catalog × ResourceManager.
        Booting empty on missing DBs is intentional — keeps the agent alive
        in dev/test environments without the library."""
        self._datasets = []
        self._by_id = {}
        if not (os.path.isfile(self._catalog_path)
                and os.path.isfile(self._rm_path)):
            return
        try:
            conn = sqlite3.connect(self._catalog_path)
            conn.execute(f"ATTACH '{self._rm_path}' AS rm")
            rows = conn.execute("""
                SELECT c.ResourceId, c.Title, c.Type, rm.Resources.Location
                FROM Records c
                JOIN rm.Resources ON c.ResourceId = rm.Resources.ResourceId
                WHERE rm.Resources.Location LIKE '%.lbs%'
            """).fetchall()
            conn.close()
        except sqlite3.Error:
            return
        for rid, title, dtype, location in rows:
            if dtype in DENYLIST_TYPES:
                continue
            filename = os.path.basename(location or "")
            family = _classify(filename)
            ds = Dataset(dataset_id=rid, title=title or "", type=dtype or "",
                         filename=filename, location=location or "",
                         schema_family=family)
            self._datasets.append(ds)
            self._by_id[rid] = ds

    def list_all(self) -> list[Dataset]:
        return list(self._datasets)

    def find(self, dataset_id: str) -> Dataset | None:
        return self._by_id.get(dataset_id)

    def by_family(self, family: str) -> list[Dataset]:
        return [d for d in self._datasets if d.schema_family == family]


# Module-level singleton (lazy).
_singleton: DatasetRegistry | None = None


def get_registry() -> DatasetRegistry:
    global _singleton
    if _singleton is None:
        _singleton = DatasetRegistry()
        _singleton.load()
    return _singleton
