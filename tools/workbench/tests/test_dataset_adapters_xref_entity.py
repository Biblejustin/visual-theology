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


def test_xref_adapter_calls_reader_with_cross_references_db_and_real_sql(monkeypatch):
    """Deeper-seam test: mock at the dataset_tools boundary so the REAL
    _query_xref_for_passage body runs. Asserts the db_name and SQL shape
    that reach the reader."""
    import dataset_tools
    captured = {}

    class _FakeReader:
        def query_dataset(self, filename, db_name, sql):
            captured['filename'] = filename
            captured['db_name'] = db_name
            captured['sql'] = sql
            return [{"to": "Hebrews 9:5", "topic": "mercy seat"}]

    monkeypatch.setattr(dataset_tools, "_get_reader", lambda: _FakeReader(), raising=False)
    out = adapter_for("xref").query(
        {"book": 66, "chapter": 3, "verse_start": 25}, _xref_ds())
    assert captured.get('filename') == "THEOLOGY-XREFS.lbsxrf"
    assert captured.get('db_name') == "CrossReferences.db"
    # Sanity: the SQL targets the CrossReferences table and uses a hex
    # clause for the passage.
    assert "CrossReferences" in (captured.get('sql') or "")
    assert out["results"] == [{"to": "Hebrews 9:5", "topic": "mercy seat"}]


def test_entity_adapter_returns_unsupported_envelope_for_events(monkeypatch):
    """Regression for the Task 3 false-empty bug: entity extensions whose
    inner schema doesn't match _query_entity_db (e.g. .lbsevt Events) must
    NOT return 'no matches' silently — they must return the explicit 'no db
    mapping' diagnostic so the agent + Bryan know we don't have an adapter
    for that family yet."""
    out = adapter_for("entity").query(
        {"book": 66, "chapter": 3, "verse_start": 25},
        _entity_ds("BiblicalEvents.lbsevt"))
    assert out["results"] == []
    note = (out.get("note") or "").lower()
    assert "no db mapping" in note, f"expected 'no db mapping' diagnostic, got: {out.get('note')!r}"


def test_entity_adapter_calls_helper_with_resolved_db_name(monkeypatch):
    """Deeper-seam test: mock at the dataset_tools boundary so the REAL
    EntityAdapter.query body runs and we can assert _query_entity_db is
    called with the resolved db_name."""
    import dataset_tools
    captured = {}
    def fake(filename, db_name, book, chapter):
        captured['filename'] = filename
        captured['db_name'] = db_name
        return [{"entity": "Cherubim"}]
    monkeypatch.setattr(dataset_tools, "_query_entity_db", fake, raising=False)
    out = adapter_for("entity").query(
        {"book": 66, "chapter": 3, "verse_start": 25}, _entity_ds("BiblicalPeople.lbsbpd"))
    assert captured.get('filename') == "BiblicalPeople.lbsbpd"
    assert captured.get('db_name') == "biblicalpeople.db"
    assert out["results"] == [{"entity": "Cherubim"}]
