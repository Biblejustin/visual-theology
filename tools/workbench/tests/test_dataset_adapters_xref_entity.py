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
