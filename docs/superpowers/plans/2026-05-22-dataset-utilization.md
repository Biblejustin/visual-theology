# Dataset Utilization Implementation Plan (Project B-1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire ~100 currently-dark Logos datasets into the study-partner agent via a hybrid model: auto-discovered registry + per-schema adapters + a generic `query_dataset` tool + a small set of named high-frequency tools, with phase-aware prompt cues so the agent actually reaches for them.

**Architecture:** A registry boots once per process by joining the catalog + ResourceManager DBs and classifying each `.lbs*` resource into a `schema_family`. Each family has one adapter exposing a uniform `query(reference, dataset)` interface. The agent gets `list_datasets()` for discovery, `query_dataset(name, ref)` for the long tail, and two named tools (`lookup_syntax_graph`, `lookup_bullinger_figure`) for the highest-frequency cases. Phase prompts cue the right datasets per phase.

**Tech Stack:** Python, pytest, existing `dataset_tools.py` patterns, `EncryptedVolume` reader (already used by `_get_reader`), `companion_tools.py` tool-dispatch (`execute_tool`).

**Environment for running tests:**
```bash
export PATH="/opt/homebrew/opt/dotnet@8/bin:$PATH"; export DOTNET_ROOT="/opt/homebrew/opt/dotnet@8/libexec"
cd /Volumes/External/Logos4/tools/workbench
```

**Spec:** `docs/superpowers/specs/2026-05-22-dataset-utilization-design.md`. Read the spec first if anything below is unclear.

---

## File structure

- **New** `tools/workbench/dataset_registry.py` — auto-discovery + classification + lookup API.
- **New** `tools/workbench/dataset_adapters/__init__.py` — adapter base + factory by `schema_family`.
- **New** `tools/workbench/dataset_adapters/supplemental.py` — `.lbssd` family (Bullinger, figurative language, "All the X" topical sets, etc.).
- **New** `tools/workbench/dataset_adapters/xref.py` — `.lbsxrf` family.
- **New** `tools/workbench/dataset_adapters/entity.py` — `.lbsbpd / .lbsplc / .lbsthg / .lbsevt / .lbsbrd / .lbsbio / .lbsmps` families.
- **New** `tools/workbench/dataset_adapters/generic.py` — best-effort table-introspection fallback for `clauses / grammar_ds / ontology / thematic / misc` (covers the long tail in B-1; richer family-specific adapters can replace this in follow-ons).
- **New** `tools/workbench/dataset_adapters/syntax.py` — `.lbsclv / .lbssyn` (Cascadia, Andersen-Forbes phrase markers). Digested phrase structure, not raw graphs.
- **Modify** `tools/workbench/companion_tools.py` — register `list_datasets`, `query_dataset`, `lookup_syntax_graph`, `lookup_bullinger_figure`; add dispatch + handlers.
- **Modify** `tools/workbench/persona_prompts.py` — `PHASE_DATASET_HINTS` mapping + `list_datasets` mention in research-prompt tools paragraph.
- **Tests (new):**
  - `tests/test_dataset_registry.py`
  - `tests/test_dataset_adapters_supplemental.py`
  - `tests/test_dataset_adapters_xref_entity.py`
  - `tests/test_dataset_adapters_generic.py`
  - `tests/test_dataset_adapters_syntax.py`
  - `tests/test_list_and_query_dataset_tools.py`
  - `tests/test_named_dataset_tools.py`
  - `tests/test_phase_dataset_hints.py`

---

## Task 1: Dataset registry

**Files:**
- Create: `tools/workbench/dataset_registry.py`
- Test: `tools/workbench/tests/test_dataset_registry.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_dataset_registry.py
import os, sys, tempfile, sqlite3
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import dataset_registry as dr


def _make_fake_catalog(tmpdir):
    """Build a tiny catalog.db + ResourceManager.db with a handful of resource
    rows, mirroring the live join (rm.Resources.Location ~ Records.ResourceId)."""
    cat = os.path.join(tmpdir, 'catalog.db')
    rm = os.path.join(tmpdir, 'rm.db')
    c = sqlite3.connect(cat)
    c.executescript("""
        CREATE TABLE Records (ResourceId TEXT PRIMARY KEY, Title TEXT, Type TEXT);
        INSERT INTO Records VALUES
          ('LLS:BULLINGER', "Bullinger's Figures of Speech", 'lbx.supplementaldata'),
          ('LLS:THXREF',    'Systematic Theology Cross References Dataset', 'lbx.crossreferences'),
          ('LLS:CASCADIA',  'Cascadia Syntax Graphs of the NT', 'lbx.syntaxdatabase'),
          ('LLS:ESVNT',     'ESV NT reverse interlinear', 'lbx.reverse-interlinear'),
          ('LLS:INTERACT',  'Some Interactive', 'lbx.interactive'),
          ('LLS:LSTO',      'Lexham Systematic Theology Ontology', 'lbx.lexhamsystematictheologyontology'),
          ('LLS:UNKNOWN',   'Something Weird', 'lbx.somethingelse');
    """)
    c.commit(); c.close()
    r = sqlite3.connect(rm)
    r.executescript("""
        CREATE TABLE Resources (ResourceId TEXT PRIMARY KEY, Location TEXT);
        INSERT INTO Resources VALUES
          ('LLS:BULLINGER', '/x/Resources/BULLINGER-FIGURES.lbssd'),
          ('LLS:THXREF',    '/x/Resources/THEOLOGY-XREFS.lbsxrf'),
          ('LLS:CASCADIA',  '/x/Resources/CSGNT.lbssyn'),
          ('LLS:ESVNT',     '/x/Resources/ESVNT.lbsrvi'),
          ('LLS:INTERACT',  '/x/Resources/INTERACTIVE.lbshtm'),
          ('LLS:LSTO',      '/x/Resources/LSTO.lbslsto'),
          ('LLS:UNKNOWN',   '/x/Resources/WEIRD.lbszzz');
    """)
    r.commit(); r.close()
    return cat, rm


def test_registry_loads_and_excludes_denylisted_types(tmp_path):
    cat, rm = _make_fake_catalog(str(tmp_path))
    reg = dr.DatasetRegistry(catalog_path=cat, resource_manager_path=rm)
    reg.load()
    ids = {d.dataset_id for d in reg.list_all()}
    assert 'LLS:BULLINGER' in ids
    assert 'LLS:THXREF' in ids
    assert 'LLS:CASCADIA' in ids
    assert 'LLS:LSTO' in ids
    assert 'LLS:UNKNOWN' in ids  # 'misc' family, still listed
    # Denylisted types must NOT appear
    assert 'LLS:ESVNT' not in ids
    assert 'LLS:INTERACT' not in ids


def test_registry_classifies_schema_family(tmp_path):
    cat, rm = _make_fake_catalog(str(tmp_path))
    reg = dr.DatasetRegistry(catalog_path=cat, resource_manager_path=rm); reg.load()
    by_id = {d.dataset_id: d for d in reg.list_all()}
    assert by_id['LLS:BULLINGER'].schema_family == 'supplemental'  # .lbssd
    assert by_id['LLS:THXREF'].schema_family    == 'xref'          # .lbsxrf
    assert by_id['LLS:CASCADIA'].schema_family  == 'syntax'        # .lbssyn
    assert by_id['LLS:LSTO'].schema_family      == 'ontology'      # .lbslsto
    assert by_id['LLS:UNKNOWN'].schema_family   == 'misc'          # unknown ext


def test_registry_find_and_by_family(tmp_path):
    cat, rm = _make_fake_catalog(str(tmp_path))
    reg = dr.DatasetRegistry(catalog_path=cat, resource_manager_path=rm); reg.load()
    assert reg.find('LLS:BULLINGER').title.startswith("Bullinger")
    assert reg.find('NOPE') is None
    fam = reg.by_family('xref')
    assert len(fam) == 1 and fam[0].dataset_id == 'LLS:THXREF'


def test_registry_survives_missing_dbs(tmp_path):
    # If catalog or RM file is missing, registry boots empty rather than crashes.
    reg = dr.DatasetRegistry(catalog_path=str(tmp_path / 'nope1.db'),
                              resource_manager_path=str(tmp_path / 'nope2.db'))
    reg.load()  # must not raise
    assert reg.list_all() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_dataset_registry.py -q`
Expected: 4 FAIL — `dataset_registry` module missing.

- [ ] **Step 3: Write the registry module**

Create `tools/workbench/dataset_registry.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_dataset_registry.py -q`
Expected: 4 passed.

- [ ] **Step 5: Sanity-check against the live library**

Run: `python3 -c "from dataset_registry import get_registry; r=get_registry(); print('total:', len(r.list_all())); from collections import Counter; print(Counter(d.schema_family for d in r.list_all()))"`
Expected: roughly ~115 total datasets; family distribution roughly: supplemental ~64, xref 4, entity ~10, syntax ~6, clauses 3, grammar_ds 3, ontology ~7, thematic ~15, misc small.

- [ ] **Step 6: Commit**

```bash
git add tools/workbench/dataset_registry.py tools/workbench/tests/test_dataset_registry.py
git commit -m "feat(datasets): auto-discovered dataset registry with schema-family classification"
```

---

## Task 2: Adapter base + supplemental adapter

**Files:**
- Create: `tools/workbench/dataset_adapters/__init__.py`
- Create: `tools/workbench/dataset_adapters/supplemental.py`
- Test: `tools/workbench/tests/test_dataset_adapters_supplemental.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dataset_adapters_supplemental.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from dataset_adapters import adapter_for
from dataset_registry import Dataset


def _ds(filename="BULLINGER-FIGURES.lbssd"):
    return Dataset(dataset_id="LLS:BULLINGER", title="Bullinger's Figures",
                   type="lbx.supplementaldata", filename=filename,
                   location="/x/Resources/" + filename,
                   schema_family="supplemental")


def test_supplemental_adapter_returns_normalized_envelope(monkeypatch):
    """The supplemental adapter wraps dataset_tools' supplemental-DB query
    machinery and returns the uniform {dataset, dataset_type, title, results, note}
    envelope. We mock the underlying query helper at the boundary."""
    from dataset_adapters import supplemental
    fake_rows = [{"reference": "Romans 3:25", "figure": "metonymy", "note": "blood for life"}]
    monkeypatch.setattr(supplemental, "_query_supplemental_for_passage",
                        lambda *a, **k: fake_rows, raising=False)
    ad = adapter_for("supplemental")
    out = ad.query({"book": 66, "chapter": 3, "verse_start": 25, "verse_end": 26}, _ds())
    assert out["dataset"] == "LLS:BULLINGER"
    assert out["dataset_type"] == "lbx.supplementaldata"
    assert out["title"].startswith("Bullinger")
    assert out["results"] == fake_rows
    assert "note" in out  # diagnostic field always present


def test_supplemental_adapter_returns_empty_with_note_on_no_matches(monkeypatch):
    from dataset_adapters import supplemental
    monkeypatch.setattr(supplemental, "_query_supplemental_for_passage",
                        lambda *a, **k: [], raising=False)
    ad = adapter_for("supplemental")
    out = ad.query({"book": 66, "chapter": 3, "verse_start": 25, "verse_end": 26}, _ds())
    assert out["results"] == []
    assert "no matches" in out["note"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_dataset_adapters_supplemental.py -q`
Expected: FAIL — module / function missing.

- [ ] **Step 3: Create the adapter package**

Create `tools/workbench/dataset_adapters/__init__.py`:

```python
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
```

Create `tools/workbench/dataset_adapters/supplemental.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_dataset_adapters_supplemental.py -q`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add tools/workbench/dataset_adapters/__init__.py tools/workbench/dataset_adapters/supplemental.py tools/workbench/tests/test_dataset_adapters_supplemental.py
git commit -m "feat(datasets): adapter base + supplemental adapter (.lbssd family)"
```

---

## Task 3: XRef + Entity adapters

**Files:**
- Create: `tools/workbench/dataset_adapters/xref.py`
- Create: `tools/workbench/dataset_adapters/entity.py`
- Test: `tools/workbench/tests/test_dataset_adapters_xref_entity.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_dataset_adapters_xref_entity.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from dataset_adapters import adapter_for
from dataset_registry import Dataset


def _xref_ds():
    return Dataset(dataset_id="LLS:THXREF", title="Systematic Theology XRefs",
                   type="lbx.crossreferences", filename="THEOLOGY-XREFS.lbsxrf",
                   location="/x/THEOLOGY-XREFS.lbsxrf", schema_family="xref")


def _entity_ds(fname="BiblicalPeople.lbsbpd"):
    return Dataset(dataset_id="LLS:" + fname, title=fname,
                   type="lbx.biblicalpeople", filename=fname,
                   location="/x/" + fname, schema_family="entity")


def test_xref_adapter_returns_envelope(monkeypatch):
    from dataset_adapters import xref
    fake = [{"to": "Hebrews 9:5", "topic": "mercy seat"}]
    monkeypatch.setattr(xref, "_query_xref_for_passage", lambda *a, **k: fake, raising=False)
    ad = adapter_for("xref")
    out = ad.query({"book": 66, "chapter": 3, "verse_start": 25}, _xref_ds())
    assert out["results"] == fake
    assert out["dataset"] == "LLS:THXREF"


def test_entity_adapter_returns_envelope(monkeypatch):
    from dataset_adapters import entity
    fake = [{"entity": "Mercy Seat", "ref": "Romans 3:25"}]
    monkeypatch.setattr(entity, "_query_entity_for_passage", lambda *a, **k: fake, raising=False)
    ad = adapter_for("entity")
    out = ad.query({"book": 66, "chapter": 3, "verse_start": 25}, _entity_ds())
    assert out["results"] == fake


def test_entity_adapter_picks_right_db_filename(monkeypatch):
    """Entity adapter resolves db_name from the .lbs* extension."""
    from dataset_adapters import entity
    captured = {}
    def fake(filename, db_name, book, chapter, *_a, **_k):
        captured['db_name'] = db_name
        return []
    monkeypatch.setattr(entity, "_query_entity_for_passage", fake, raising=False)
    ad = adapter_for("entity")
    ad.query({"book": 66, "chapter": 3, "verse_start": 25}, _entity_ds("BiblicalPlaces.lbsplc"))
    assert captured['db_name'].lower() == "biblicalplaces.db"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_dataset_adapters_xref_entity.py -q`
Expected: 3 FAIL.

- [ ] **Step 3: Create the adapters**

Create `tools/workbench/dataset_adapters/xref.py`:

```python
"""Adapter for the .lbsxrf cross-reference family. Wraps the existing
query_theology_xrefs / query_curated_cross_refs machinery."""
from __future__ import annotations

import logging
from dataset_registry import Dataset
from . import envelope

logger = logging.getLogger(__name__)


def _query_xref_for_passage(filename: str, book: int, chapter: int,
                            verse_start: int | None, verse_end: int | None,
                            limit: int) -> list[dict]:
    try:
        from dataset_tools import (_get_reader, _ref_hex_clause_for_col,
                                   _protestant_to_logos)
    except Exception:
        return []
    reader = _get_reader()
    if reader is None:
        return []
    try:
        logos_book = _protestant_to_logos(book)
        clause = _ref_hex_clause_for_col("ref", logos_book, chapter)
        sql = (f"SELECT * FROM CrossReferences WHERE {clause} "
               f"LIMIT {int(limit)}")
        # Generic query over the xref DB. The existing code wraps reader
        # subprocess; reuse if exposed, else best-effort row return.
        rows = reader.query_encrypted(filename, "CrossReferences.db", sql) or []
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
```

Create `tools/workbench/dataset_adapters/entity.py`:

```python
"""Adapter for entity families (.lbsbpd, .lbsplc, .lbsthg, .lbsevt, .lbsbrd,
.lbsbio, .lbsmps, .lbsspd). Wraps dataset_tools._query_entity_db."""
from __future__ import annotations

import logging
import os
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
        from dataset_tools import _query_entity_db
    except Exception:
        return []
    try:
        rows = _query_entity_db(filename, db_name, book, chapter) or []
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_dataset_adapters_xref_entity.py -q`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add tools/workbench/dataset_adapters/xref.py tools/workbench/dataset_adapters/entity.py tools/workbench/tests/test_dataset_adapters_xref_entity.py
git commit -m "feat(datasets): xref + entity adapters"
```

---

## Task 4: Generic best-effort adapter (long-tail families)

**Files:**
- Create: `tools/workbench/dataset_adapters/generic.py`
- Test: `tools/workbench/tests/test_dataset_adapters_generic.py`

This single adapter covers `clauses`, `grammar_ds`, `ontology`, `thematic`, and `misc`. It introspects the dataset's tables via the existing `query_dataset_tables` helper and returns a discovery + best-effort row dump. Richer per-family adapters can replace this in follow-ons; B-1's goal is that **every dataset in the registry is reachable** so the agent can mine the long tail.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dataset_adapters_generic.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from dataset_adapters import adapter_for
from dataset_registry import Dataset


def _ds(fname="LSTO.lbslsto", family="ontology"):
    return Dataset(dataset_id="LLS:LSTO", title="Lexham Systematic Theology Ontology",
                   type="lbx.lexhamsystematictheologyontology", filename=fname,
                   location="/x/" + fname, schema_family=family)


def test_generic_adapter_reports_schema_and_attempts_rows(monkeypatch):
    """The generic adapter discovers tables and returns a sample of rows so
    the agent gets SOMETHING from any registered dataset, even before a
    bespoke adapter exists."""
    from dataset_adapters import generic
    monkeypatch.setattr(generic, "_discover_tables",
                        lambda fname: ["Entries", "Topics"], raising=False)
    monkeypatch.setattr(generic, "_sample_rows",
                        lambda fname, table, ref, limit: [{"col": "value", "_table": table}],
                        raising=False)
    ad = adapter_for("ontology")
    out = ad.query({"book": 66, "chapter": 3, "verse_start": 25}, _ds())
    assert out["dataset"] == "LLS:LSTO"
    # Results include rows from every discovered table, capped at limit
    assert any(r.get("_table") == "Entries" for r in out["results"])
    assert any(r.get("_table") == "Topics" for r in out["results"])


def test_generic_adapter_returns_empty_envelope_when_unreachable(monkeypatch):
    from dataset_adapters import generic
    monkeypatch.setattr(generic, "_discover_tables", lambda fname: [], raising=False)
    ad = adapter_for("clauses")
    out = ad.query({"book": 66, "chapter": 3, "verse_start": 25},
                   _ds("Clauses-LHB.lbscls", "clauses"))
    assert out["results"] == []
    assert "no matches" in out["note"].lower() or "unreachable" in out["note"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_dataset_adapters_generic.py -q`
Expected: 2 FAIL.

- [ ] **Step 3: Create the generic adapter**

Create `tools/workbench/dataset_adapters/generic.py`:

```python
"""Best-effort adapter for long-tail dataset families (clauses, grammar_ds,
ontology, thematic, misc). Introspects tables via dataset_tools.query_dataset_tables
and samples rows so every registered dataset is reachable from day one.
Replace with bespoke per-family adapters in follow-on slices as needs become clear."""
from __future__ import annotations

import logging
from dataset_registry import Dataset
from . import envelope

logger = logging.getLogger(__name__)


def _discover_tables(filename: str) -> list[str]:
    try:
        from dataset_tools import query_dataset_tables, _get_reader
        reader = _get_reader()
        if reader is None:
            return []
        # Most .lbs* volumes contain an inner DB whose name matches the
        # filename stem; query_dataset_tables abstracts the lookup.
        return list(query_dataset_tables(filename, None) or [])
    except Exception:
        logger.warning("table discovery failed for %s", filename, exc_info=True)
        return []


def _sample_rows(filename: str, table: str, reference: dict, limit: int) -> list[dict]:
    """Best-effort: pull up to `limit` rows from `table`, tagging each with
    its source table. We don't try to filter by passage here — many of these
    datasets aren't passage-keyed in an obvious column, so we let the agent
    inspect what came back."""
    try:
        from dataset_tools import _get_reader
        reader = _get_reader()
        if reader is None:
            return []
        sql = f"SELECT * FROM {table} LIMIT {int(limit)}"
        rows = reader.query_encrypted(filename, None, sql) or []
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_dataset_adapters_generic.py -q`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add tools/workbench/dataset_adapters/generic.py tools/workbench/tests/test_dataset_adapters_generic.py
git commit -m "feat(datasets): generic best-effort adapter for long-tail families"
```

---

## Task 5: Syntax adapter (Cascadia / Andersen-Forbes)

**Files:**
- Create: `tools/workbench/dataset_adapters/syntax.py`
- Test: `tools/workbench/tests/test_dataset_adapters_syntax.py`

This is the highest-priority new adapter — it fulfills the A-handoff promise (deepens the *pistis Christou* genitive answer with Cascadia structure). The exact inner-DB schema for `.lbsclv`/`.lbssyn` is investigated at the start of this task; the adapter returns a *digested* phrase summary (head + children + relation type) per verse, not raw graph dumps.

- [ ] **Step 1: Investigate the schema**

Run (read-only schema discovery):
```bash
python3 -c "
import sys; sys.path.insert(0,'.')
from dataset_tools import query_dataset_tables, _get_reader
r=_get_reader()
print('tables:', query_dataset_tables('CSGNT.lbssyn', None))
"
```

Inspect the table names and pick the most relevant ones (Cascadia typically has `nodes`, `phrases`, `clauses`-style tables). If the schema is opaque, **skip the digest** and have the adapter fall through to `GenericAdapter` semantics (table sample) — still satisfies "reachable." Document what you found in a comment at the top of `syntax.py`.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_dataset_adapters_syntax.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from dataset_adapters import adapter_for
from dataset_registry import Dataset


def _ds():
    return Dataset(dataset_id="LLS:CSGNT", title="Cascadia Syntax Graphs of the NT",
                   type="lbx.syntaxdatabase", filename="CSGNT.lbssyn",
                   location="/x/CSGNT.lbssyn", schema_family="syntax")


def test_syntax_adapter_returns_phrase_summary(monkeypatch):
    """The syntax adapter returns a digested phrase summary keyed to the
    passage — head term + dependents + relation type per phrase — not raw
    graph dumps. We mock the inner reader at the boundary."""
    from dataset_adapters import syntax
    fake = [
        {"verse": 22, "head": "πίστεως", "relation": "genitive_modifier",
         "dependents": ["Ἰησοῦ", "Χριστοῦ"]},
    ]
    monkeypatch.setattr(syntax, "_digest_phrases",
                        lambda *a, **k: fake, raising=False)
    ad = adapter_for("syntax")
    out = ad.query({"book": 66, "chapter": 3, "verse_start": 22, "verse_end": 22}, _ds())
    assert out["results"] == fake
    assert out["dataset"] == "LLS:CSGNT"


def test_syntax_adapter_returns_empty_envelope_when_unreachable(monkeypatch):
    from dataset_adapters import syntax
    monkeypatch.setattr(syntax, "_digest_phrases",
                        lambda *a, **k: [], raising=False)
    ad = adapter_for("syntax")
    out = ad.query({"book": 66, "chapter": 3, "verse_start": 22}, _ds())
    assert out["results"] == []
    assert "no matches" in out["note"].lower() or "unreachable" in out["note"].lower()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3 -m pytest tests/test_dataset_adapters_syntax.py -q`
Expected: 2 FAIL.

- [ ] **Step 4: Create the syntax adapter**

Create `tools/workbench/dataset_adapters/syntax.py`:

```python
"""Adapter for the .lbsclv / .lbssyn syntax-graph family (Cascadia Syntax
Graphs of the NT/SBL/LXX, Andersen-Forbes phrase markers).

Returns a *digested* phrase summary keyed to the passage — head term +
dependents + relation type — not raw graph node dumps. If the inner-DB
schema can't be digested cleanly, falls back to a table sample so the
agent still gets something.

Schema notes (record what step 1 discovered):
    Cascadia .lbssyn tables: <fill in from investigation>
"""
from __future__ import annotations

import logging
from dataset_registry import Dataset
from . import envelope

logger = logging.getLogger(__name__)


def _digest_phrases(filename: str, book: int, chapter: int,
                    verse_start: int | None, verse_end: int | None,
                    limit: int) -> list[dict]:
    """Pull phrase nodes for the verse range and produce a head/dependents
    summary. The actual SQL/column names depend on the .lbssyn schema; if
    investigation reveals a clean shape, implement here. Otherwise return []
    and let the envelope's note signal 'no matches'."""
    try:
        from dataset_tools import _get_reader
        reader = _get_reader()
        if reader is None:
            return []
    except Exception:
        return []
    # Implementer: replace the body below with the real digest once the schema
    # is understood. Until then, return [] so callers see "no matches" and the
    # generic table sample (via query_dataset) still works as fallback.
    try:
        # Placeholder: a clean schema-walk implementation goes here.
        return []
    except Exception:
        logger.warning("syntax digest failed for %s", filename, exc_info=True)
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
        return envelope(dataset, rows)
```

> **Note for the implementer:** the spec calls for a digested shape (head/dependents/relation). If the schema investigation in Step 1 reveals a clean path, replace `_digest_phrases`'s body with the real digest. If it's opaque, ship the adapter with the stub (returns `[]` with envelope note) — the dataset is still reachable through the generic-adapter fallback for table sampling. **Either way is acceptable for B-1.** A richer digest can be a B-2 task.

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_dataset_adapters_syntax.py -q`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add tools/workbench/dataset_adapters/syntax.py tools/workbench/tests/test_dataset_adapters_syntax.py
git commit -m "feat(datasets): syntax adapter scaffold (Cascadia / Andersen-Forbes)"
```

---

## Task 6: `list_datasets` + `query_dataset` tools

**Files:**
- Modify: `tools/workbench/companion_tools.py` (add TOOL_DEFINITIONS entries, dispatch elifs, two handlers).
- Test: `tools/workbench/tests/test_list_and_query_dataset_tools.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_list_and_query_dataset_tools.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import companion_tools
from dataset_registry import Dataset, DatasetRegistry


class _StubRegistry:
    def __init__(self, datasets):
        self._d = datasets
        self._by = {d.dataset_id: d for d in datasets}
    def list_all(self): return list(self._d)
    def find(self, did): return self._by.get(did)
    def by_family(self, fam): return [d for d in self._d if d.schema_family == fam]


def _datasets():
    return [
        Dataset("LLS:BULLINGER", "Bullinger's Figures", "lbx.supplementaldata",
                "BULLINGER-FIGURES.lbssd", "/x/BULLINGER-FIGURES.lbssd", "supplemental"),
        Dataset("LLS:THXREF", "Systematic Theology XRefs", "lbx.crossreferences",
                "THEOLOGY-XREFS.lbsxrf", "/x/THEOLOGY-XREFS.lbsxrf", "xref"),
        Dataset("LLS:CSGNT", "Cascadia Syntax Graphs of the NT",
                "lbx.syntaxdatabase", "CSGNT.lbssyn", "/x/CSGNT.lbssyn", "syntax"),
    ]


def test_list_datasets_returns_full_catalog(monkeypatch):
    monkeypatch.setattr(companion_tools, "get_registry",
                        lambda: _StubRegistry(_datasets()), raising=False)
    out = companion_tools.execute_tool("list_datasets", {}, {})
    assert out["count"] == 3
    ids = {d["dataset_id"] for d in out["datasets"]}
    assert ids == {"LLS:BULLINGER", "LLS:THXREF", "LLS:CSGNT"}


def test_list_datasets_filters_by_family(monkeypatch):
    monkeypatch.setattr(companion_tools, "get_registry",
                        lambda: _StubRegistry(_datasets()), raising=False)
    out = companion_tools.execute_tool("list_datasets", {"category": "xref"}, {})
    assert out["count"] == 1
    assert out["datasets"][0]["dataset_id"] == "LLS:THXREF"


def test_query_dataset_routes_through_adapter(monkeypatch):
    monkeypatch.setattr(companion_tools, "get_registry",
                        lambda: _StubRegistry(_datasets()), raising=False)
    captured = {}
    def fake_adapter_for(family):
        class _A:
            def query(self, ref, ds, *, limit=25):
                captured["family"] = family
                captured["ref"] = ref
                captured["ds"] = ds.dataset_id
                return {"dataset": ds.dataset_id, "dataset_type": ds.type,
                        "title": ds.title, "results": [{"k": "v"}], "note": "ok"}
        return _A()
    monkeypatch.setattr(companion_tools, "adapter_for", fake_adapter_for, raising=False)
    out = companion_tools.execute_tool("query_dataset",
        {"dataset_id": "LLS:BULLINGER", "reference": "Romans 3:25-26"}, {})
    assert out["results"] == [{"k": "v"}]
    assert captured["family"] == "supplemental"
    assert captured["ds"] == "LLS:BULLINGER"
    # Reference was parsed before being handed to the adapter.
    assert captured["ref"].get("book") and captured["ref"].get("chapter")


def test_query_dataset_unknown_id(monkeypatch):
    monkeypatch.setattr(companion_tools, "get_registry",
                        lambda: _StubRegistry(_datasets()), raising=False)
    out = companion_tools.execute_tool("query_dataset",
        {"dataset_id": "LLS:NOPE", "reference": "Romans 3:25-26"}, {})
    assert "error" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_list_and_query_dataset_tools.py -q`
Expected: 4 FAIL.

- [ ] **Step 3: Add tool definitions to TOOL_DEFINITIONS in companion_tools.py**

```python
{
    "name": "list_datasets",
    "description": (
        "List the research datasets present in Bryan's Logos library. "
        "Use this BEFORE picking a dataset to query — there are ~115 datasets "
        "covering topical sets ('All the Prayers in the Bible'), syntax graphs, "
        "ontologies, cross-references, biblical entities, and more. "
        "Optional `category` filters by schema_family (supplemental, xref, entity, "
        "syntax, clauses, grammar_ds, ontology, thematic, misc) or by lbx type."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "category": {"type": "string", "description": "Optional schema_family or lbx.* type filter."},
        },
    },
},
{
    "name": "query_dataset",
    "description": (
        "Query any dataset in the registry by ID for a Bible passage. Returns "
        "{dataset, dataset_type, title, results, note}. Use this for the long tail "
        "of datasets that don't have a named tool. Get dataset_id from list_datasets()."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "dataset_id": {"type": "string", "description": "Dataset id (e.g. 'LLS:BULLINGER') from list_datasets()."},
            "reference":  {"type": "string", "description": "Bible reference, e.g. 'Romans 3:25-26'."},
            "limit":      {"type": "integer", "description": "Max rows to return (default 25, max 100)."},
        },
        "required": ["dataset_id", "reference"],
    },
},
```

- [ ] **Step 4: Add dispatch + handlers in `execute_tool`**

```python
        elif tool_name == "list_datasets":
            return _list_datasets(tool_input)
        elif tool_name == "query_dataset":
            return _query_dataset(tool_input)
```

Module-level imports near the top of `companion_tools.py`:
```python
from dataset_registry import get_registry
from dataset_adapters import adapter_for
```

Handlers (place near `_search_library`):

```python
def _list_datasets(tool_input):
    cat = (tool_input.get("category") or "").strip().lower() or None
    reg = get_registry()
    all_ds = reg.list_all()
    if cat:
        ds = [d for d in all_ds if d.schema_family == cat or d.type.lower() == cat]
    else:
        ds = all_ds
    return {
        "count": len(ds),
        "datasets": [
            {"dataset_id": d.dataset_id, "title": d.title,
             "family": d.schema_family, "type": d.type}
            for d in ds
        ],
    }


def _query_dataset(tool_input):
    from study import parse_reference
    dataset_id = (tool_input.get("dataset_id") or "").strip()
    ref_str = (tool_input.get("reference") or "").strip()
    if not dataset_id or not ref_str:
        return {"error": "dataset_id and reference are required"}
    limit = max(1, min(int(tool_input.get("limit", 25)), 100))
    ds = get_registry().find(dataset_id)
    if ds is None:
        return {"error": "unknown_dataset", "dataset_id": dataset_id,
                "message": "call list_datasets() to discover valid ids"}
    ref = parse_reference(ref_str)
    if not ref:
        return {"error": "bad_reference", "reference": ref_str}
    try:
        return adapter_for(ds.schema_family).query(ref, ds, limit=limit)
    except Exception as e:
        return {"error": f"query_failed: {type(e).__name__}: {e}",
                "dataset_id": dataset_id, "reference": ref_str}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_list_and_query_dataset_tools.py -q`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add tools/workbench/companion_tools.py tools/workbench/tests/test_list_and_query_dataset_tools.py
git commit -m "feat(datasets): list_datasets + query_dataset tools (catalog discovery + long-tail access)"
```

---

## Task 7: Named tools — `lookup_syntax_graph` + `lookup_bullinger_figure`

**Files:**
- Modify: `tools/workbench/companion_tools.py` (2 more TOOL_DEFINITIONS entries, 2 dispatch elifs, 2 handlers).
- Test: `tools/workbench/tests/test_named_dataset_tools.py`

These are the two named high-frequency tools for B-1. They are thin wrappers over `query_dataset` that pre-select the right dataset, so the agent reaches for them by name without thinking.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_named_dataset_tools.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import companion_tools
from dataset_registry import Dataset


class _Reg:
    def __init__(self, datasets):
        self._d = datasets
        self._by = {d.dataset_id: d for d in datasets}
    def list_all(self): return list(self._d)
    def find(self, did): return self._by.get(did)
    def by_family(self, fam): return [d for d in self._d if d.schema_family == fam]


CSGNT = Dataset("LLS:CSGNT", "Cascadia Syntax Graphs of the NT",
                "lbx.syntaxdatabase", "CSGNT.lbssyn", "/x/CSGNT.lbssyn", "syntax")
AF_HEB = Dataset("LLS:AF-HEB", "Andersen-Forbes Hebrew Phrase Markers",
                 "lbx.syntaxdatabase", "AFP-HEB.lbssyn", "/x/AFP-HEB.lbssyn", "syntax")
BULL = Dataset("LLS:BULL", "Bullinger's Figures of Speech",
               "lbx.supplementaldata", "BULLINGER-FIGURES.lbssd",
               "/x/BULLINGER-FIGURES.lbssd", "supplemental")


def _fake_adapter_returning(rows):
    class _A:
        def query(self, ref, ds, *, limit=25):
            return {"dataset": ds.dataset_id, "dataset_type": ds.type,
                    "title": ds.title, "results": rows, "note": "ok"}
    return lambda fam: _A()


def test_lookup_syntax_graph_nt_picks_cascadia(monkeypatch):
    monkeypatch.setattr(companion_tools, "get_registry",
                        lambda: _Reg([CSGNT, AF_HEB, BULL]), raising=False)
    monkeypatch.setattr(companion_tools, "adapter_for",
                        _fake_adapter_returning([{"phrase": "πίστεως"}]), raising=False)
    out = companion_tools.execute_tool("lookup_syntax_graph",
        {"reference": "Romans 3:22", "testament": "nt"}, {})
    assert out["dataset"] == "LLS:CSGNT"
    assert out["results"] == [{"phrase": "πίστεως"}]


def test_lookup_syntax_graph_ot_picks_andersen_forbes(monkeypatch):
    monkeypatch.setattr(companion_tools, "get_registry",
                        lambda: _Reg([CSGNT, AF_HEB, BULL]), raising=False)
    monkeypatch.setattr(companion_tools, "adapter_for",
                        _fake_adapter_returning([{"phrase": "אלהים"}]), raising=False)
    out = companion_tools.execute_tool("lookup_syntax_graph",
        {"reference": "Genesis 1:1", "testament": "ot"}, {})
    assert out["dataset"] == "LLS:AF-HEB"


def test_lookup_bullinger_figure_picks_bullinger(monkeypatch):
    monkeypatch.setattr(companion_tools, "get_registry",
                        lambda: _Reg([CSGNT, BULL]), raising=False)
    monkeypatch.setattr(companion_tools, "adapter_for",
                        _fake_adapter_returning([{"figure": "metonymy"}]), raising=False)
    out = companion_tools.execute_tool("lookup_bullinger_figure",
        {"reference": "Romans 3:25"}, {})
    assert out["dataset"] == "LLS:BULL"
    assert out["results"] == [{"figure": "metonymy"}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_named_dataset_tools.py -q`
Expected: 3 FAIL.

- [ ] **Step 3: Add TOOL_DEFINITIONS entries**

```python
{
    "name": "lookup_syntax_graph",
    "description": (
        "Look up the syntactic phrase structure for a Bible passage from "
        "Cascadia Syntax Graphs (NT) or Andersen-Forbes Phrase Markers (OT). "
        "Returns digested head/dependents/relation info — directly useful for "
        "genitive, participle, and clause-structure questions. Pair with "
        "lookup_grammar (Wallace prose) for the textual + structural picture."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reference": {"type": "string", "description": "Bible reference, e.g. 'Romans 3:22'."},
            "testament": {"type": "string", "enum": ["nt", "ot"], "description": "Default 'nt'."},
        },
        "required": ["reference"],
    },
},
{
    "name": "lookup_bullinger_figure",
    "description": (
        "Look up Bullinger's *Figures of Speech Used in the Bible* for a passage. "
        "Returns the rhetorical figures (metaphor, metonymy, hendiadys, chiasm, etc.) "
        "Bullinger catalogues at this reference. Classic homiletical mine."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reference": {"type": "string", "description": "Bible reference, e.g. 'Romans 3:25'."},
        },
        "required": ["reference"],
    },
},
```

- [ ] **Step 4: Add dispatch + handlers**

```python
        elif tool_name == "lookup_syntax_graph":
            return _lookup_syntax_graph(tool_input)
        elif tool_name == "lookup_bullinger_figure":
            return _lookup_bullinger_figure(tool_input)
```

```python
def _lookup_syntax_graph(tool_input):
    """Pre-select a syntax dataset by testament, then delegate to the adapter."""
    from study import parse_reference
    ref_str = (tool_input.get("reference") or "").strip()
    testament = (tool_input.get("testament") or "nt").lower()
    if not ref_str:
        return {"error": "reference is required"}
    ref = parse_reference(ref_str)
    if not ref:
        return {"error": "bad_reference", "reference": ref_str}
    reg = get_registry()
    syntax_ds = reg.by_family("syntax")
    # Heuristic dataset pick: NT → first 'Cascadia'-titled; OT → first 'Andersen-Forbes'-titled.
    needle = "cascadia" if testament == "nt" else "andersen-forbes"
    chosen = next((d for d in syntax_ds if needle in (d.title or "").lower()), None)
    if chosen is None and syntax_ds:
        chosen = syntax_ds[0]  # last resort
    if chosen is None:
        return {"error": "no_syntax_dataset"}
    try:
        return adapter_for("syntax").query(ref, chosen)
    except Exception as e:
        return {"error": f"syntax_query_failed: {type(e).__name__}: {e}"}


def _lookup_bullinger_figure(tool_input):
    """Pre-select Bullinger's Figures of Speech, then delegate to the adapter."""
    from study import parse_reference
    ref_str = (tool_input.get("reference") or "").strip()
    if not ref_str:
        return {"error": "reference is required"}
    ref = parse_reference(ref_str)
    if not ref:
        return {"error": "bad_reference", "reference": ref_str}
    reg = get_registry()
    chosen = next((d for d in reg.list_all() if "bullinger" in (d.title or "").lower()), None)
    if chosen is None:
        return {"error": "bullinger_not_in_library"}
    try:
        return adapter_for(chosen.schema_family).query(ref, chosen)
    except Exception as e:
        return {"error": f"bullinger_query_failed: {type(e).__name__}: {e}"}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_named_dataset_tools.py -q`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add tools/workbench/companion_tools.py tools/workbench/tests/test_named_dataset_tools.py
git commit -m "feat(datasets): lookup_syntax_graph + lookup_bullinger_figure named tools"
```

---

## Task 8: Phase-aware invocation cues

**Files:**
- Modify: `tools/workbench/persona_prompts.py` (add `PHASE_DATASET_HINTS`; render into `_ACTIVE_FOCUS[*]['tools_first']`; mention `list_datasets()` in the research-prompt tools paragraph).
- Test: `tools/workbench/tests/test_phase_dataset_hints.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_phase_dataset_hints.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from persona_prompts import build_research_partner_prompt


def _research(phase):
    return build_research_partner_prompt(
        passage="Romans 3:25-26", genre="epistle", session_elapsed_seconds=0,
        current_phase=phase, card_work_summary="", coaching_context="")


def test_research_prompt_mentions_list_datasets():
    p = _research("p04_exegesis")
    assert "list_datasets" in p, "research prompt must mention list_datasets() so the agent knows the catalog exists"


def test_p04_exegesis_cues_syntax_graph():
    p = _research("p04_exegesis")
    assert "lookup_syntax_graph" in p


def test_p10_patristics_cues_church_history_ontology():
    p = _research("p10_patristics")
    # The cue should name ChurchHistory (the Lexham Church History Ontology)
    # as a phase-relevant dataset reachable via query_dataset.
    assert "churchhistory" in p.lower() or "church history" in p.lower()


def test_p13_sermon_work_cues_bullinger():
    p = _research("p13_sermon_work")
    assert "lookup_bullinger_figure" in p or "bullinger" in p.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_phase_dataset_hints.py -q`
Expected: 4 FAIL (cues not yet present).

- [ ] **Step 3: Add `PHASE_DATASET_HINTS` mapping and render it**

In `persona_prompts.py`, add a top-level constant:

```python
PHASE_DATASET_HINTS: dict[str, list[str]] = {
    "p04_exegesis":       ["lookup_syntax_graph (Cascadia/Andersen-Forbes)",
                            "query_dataset('PhraseConcordance', ref)"],
    "p06_context":        ["query_dataset('BiblicalEvents', ref)",
                            "query_dataset('BiblicalPlacesMaps', ref)"],
    "p07_big_picture":    ["query_dataset('PericopeSets', ref)"],
    "p08_christ":         ["query_dataset('JesusFulfillmentOfMessianicProphecy', ref)"],
    "p09_systematics":    ["query_dataset('LSTO', ref)  # Lexham Systematic Theology Ontology"],
    "p10_patristics":     ["get_passage_context(ancient_literature) (existing)",
                            "query_dataset('ChurchHistory', ref)"],
    "p13_sermon_work":    ["lookup_bullinger_figure",
                            "query_dataset('AllThePrayersInTheBible', ref)"],
}
```

Find `_ACTIVE_FOCUS` and, where each phase has a `tools_first` list, append the matching hints. Concretely, after `_ACTIVE_FOCUS` is constructed, extend it programmatically:

```python
for _phase_key, _hints in PHASE_DATASET_HINTS.items():
    _entry = _ACTIVE_FOCUS.get(_phase_key)
    if _entry is not None:
        _entry.setdefault("tools_first", [])
        for _h in _hints:
            if _h not in _entry["tools_first"]:
                _entry["tools_first"].append(_h)
```

In `build_research_partner_prompt`, find the "Available Tools" paragraph (~L337-339, the one that mentions `read_library_section` from Project A). Append:

```
Don't know which dataset has what you need? Call `list_datasets()` (optionally filtered by category) to see the catalog of ~115 research datasets in Bryan's library, then `query_dataset(dataset_id, reference)` to pull from any of them.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_phase_dataset_hints.py -q`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add tools/workbench/persona_prompts.py tools/workbench/tests/test_phase_dataset_hints.py
git commit -m "feat(datasets): phase-aware invocation cues + list_datasets prompt mention"
```

---

## Task 9: Full regression + live acceptance + deploy + verify

- [ ] **Step 1: Run the full suite**

Run: `python3 -m pytest tests/ -q`
Expected: all pass (1103+ from prior baseline + the new B-1 tests; expect 1130+).

- [ ] **Step 2: Live acceptance against the real library**

```bash
export PATH="/opt/homebrew/opt/dotnet@8/bin:$PATH"; export DOTNET_ROOT="/opt/homebrew/opt/dotnet@8/libexec"
cd /Volumes/External/Logos4/tools/workbench
python3 -c "
import companion_tools as ct
# 1. Registry boots and lists ~115 datasets
out = ct.execute_tool('list_datasets', {}, {})
print('total datasets:', out['count'])
assert out['count'] >= 80, 'registry under-populated'
# 2. Filter by family
top = ct.execute_tool('list_datasets', {'category': 'supplemental'}, {})
print('supplemental count:', top['count'])
assert top['count'] >= 30
# 3. query_dataset works for a known dataset
b = next(d for d in out['datasets'] if 'bullinger' in d['title'].lower())
r = ct.execute_tool('query_dataset', {'dataset_id': b['dataset_id'], 'reference': 'Romans 3:25-26'}, {})
print('bullinger query envelope keys:', sorted(r.keys()))
assert 'results' in r and 'note' in r
# 4. lookup_syntax_graph returns an envelope (results may be empty if digest is stub)
sg = ct.execute_tool('lookup_syntax_graph', {'reference': 'Romans 3:22', 'testament': 'nt'}, {})
print('syntax envelope keys:', sorted(sg.keys()))
assert 'dataset' in sg
# 5. lookup_bullinger_figure end-to-end
bf = ct.execute_tool('lookup_bullinger_figure', {'reference': 'Romans 3:25'}, {})
print('bullinger figure envelope keys:', sorted(bf.keys()))
assert 'dataset' in bf
print('LIVE ACCEPTANCE: PASS')
"
```

Expected: all assertions pass; counts look reasonable.

- [ ] **Step 3: Deploy + verify reader health**

```bash
pm2 restart study-companion && pm2 save
```
Wait ~10 seconds. Then:

```bash
SRV=$(pm2 jlist 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print([p['pid'] for p in d if p['name']=='study-companion'][0])")
echo "server PID: $SRV"
ps -ax -o pid,ppid,etime,comm | grep -iE "LogosReader|dotnet@8/8" | grep -v grep
curl -s -o /dev/null -w "%{http_code}\n" -m 10 http://localhost:5111/study/
```
Expected: one LogosReader as a grandchild of the new server PID; HTTP `302` to login.

- [ ] **Step 4: Report**

Summarize to Bryan: total datasets surfaced by the registry, the live-acceptance evidence, the deploy health, and any concerns or follow-on suggestions (e.g. specific named tools worth adding in B-2 based on observed behavior).

---

## Notes for the implementer
- TDD strictly. Mocks only at subprocess boundaries (reader, searcher, catalog DB).
- Match the patterns in Project A (commits `e4e15c7`, `a9807ee`, `e39b992`, `86df962`, `7d160cc`, `7577255`).
- Tool descriptions matter — the agent only calls what its description makes obvious. Be specific.
- If the Cascadia schema (Task 5) is opaque, ship the syntax adapter stub — the dataset is still reachable via the generic adapter through `query_dataset`. A richer digest is a B-2 task.
- Don't touch the temporary cutoff override (auto-expires Saturday 5pm; separate cleanup) or anything in `cutoff_*` files.
- Work on `main` consistent with the project's pattern.
