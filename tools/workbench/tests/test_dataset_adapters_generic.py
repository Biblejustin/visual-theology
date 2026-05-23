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


def test_generic_adapter_deep_seam_calls_dataset_tools(monkeypatch):
    """Deeper-seam regression: mock at the dataset_tools boundary so the
    REAL _discover_tables and _sample_rows bodies execute. Tasks 2+3 had
    false-green bugs hidden behind shallow mocks; this guards Task 4."""
    import dataset_tools
    captured = {"tables_calls": [], "reader_calls": []}

    def fake_qdt(filename, db_name=None):
        captured["tables_calls"].append((filename, db_name))
        return ["Entries"]

    class _FakeReader:
        def query_dataset(self, filename, db_name, sql):
            captured["reader_calls"].append((filename, db_name, sql))
            return [{"id": 1, "topic": "propitiation"}]

    monkeypatch.setattr(dataset_tools, "query_dataset_tables", fake_qdt, raising=False)
    monkeypatch.setattr(dataset_tools, "_get_reader", lambda: _FakeReader(), raising=False)

    out = adapter_for("ontology").query(
        {"book": 66, "chapter": 3, "verse_start": 25}, _ds())

    # query_dataset_tables was called with the dataset filename.
    assert captured["tables_calls"], "query_dataset_tables not invoked"
    assert captured["tables_calls"][0][0] == "LSTO.lbslsto"
    # reader.query_dataset was called for the discovered table.
    assert captured["reader_calls"], "reader.query_dataset not invoked"
    assert captured["reader_calls"][0][0] == "LSTO.lbslsto"
    # Rows are tagged with _table.
    assert out["results"] and out["results"][0].get("_table") == "Entries"


def test_generic_adapter_handles_dataset_tools_unreachable(monkeypatch):
    """If query_dataset_tables raises or _get_reader returns None, the
    adapter returns the standard envelope with a diagnostic note instead of
    crashing."""
    import dataset_tools
    monkeypatch.setattr(dataset_tools, "query_dataset_tables",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
                        raising=False)
    monkeypatch.setattr(dataset_tools, "_get_reader", lambda: None, raising=False)
    out = adapter_for("thematic").query(
        {"book": 66, "chapter": 3, "verse_start": 25}, _ds("X.lbsptd", "thematic"))
    assert out["results"] == []
    assert "unreachable" in out["note"].lower() or "no matches" in out["note"].lower()
