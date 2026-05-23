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


def test_supplemental_adapter_uses_word_cache_string_refs(monkeypatch):
    """Regression for fdbd2c1 bug: _query_supplemental_for_passage was passing
    (book, chapter, verse) TUPLES to _find_word_ids, which expects STRING refs
    like 'gnt/1234.5:6'. Mock at the cache + _find_word_ids seam so the real
    word_refs construction in the adapter runs."""
    import dataset_tools

    captured = {}

    class _FakeCache:
        def get_word_numbers(self, book, chapter, vstart, vend):
            captured['cache_call'] = (book, chapter, vstart, vend)
            return ["gnt/1234.5:6", "gnt/1234.5:7"]

    class _FakeReader:
        pass

    def fake_find(reader, fname, db, refs):
        captured['find_refs'] = refs
        return [101, 102]

    def fake_query(reader, fname, db, ids):
        captured['query_ids'] = ids
        return [{"reference": "Romans 3:25", "figure": "metonymy"}]

    monkeypatch.setattr(dataset_tools, "_get_word_cache", lambda: _FakeCache(), raising=False)
    monkeypatch.setattr(dataset_tools, "_get_reader", lambda: _FakeReader(), raising=False)
    monkeypatch.setattr(dataset_tools, "_find_word_ids", fake_find, raising=False)
    monkeypatch.setattr(dataset_tools, "_query_supplemental_by_word_ids", fake_query, raising=False)

    from dataset_adapters import adapter_for
    out = adapter_for("supplemental").query(
        {"book": 66, "chapter": 3, "verse_start": 25, "verse_end": 26}, _ds())

    # The cache was called with the passage range — proves cache path is wired.
    assert captured.get('cache_call') == (66, 3, 25, 26)
    # The refs passed to _find_word_ids are the cache's STRINGS, not tuples.
    assert captured.get('find_refs') == ["gnt/1234.5:6", "gnt/1234.5:7"]
    # The downstream supplemental query received the word_ids.
    assert captured.get('query_ids') == [101, 102]
    # The result propagates.
    assert out["results"] == [{"reference": "Romans 3:25", "figure": "metonymy"}]


def test_adapter_for_misc_falls_back_gracefully_when_generic_missing():
    """Until Task 4 lands generic.py, adapter_for('misc') must NOT crash —
    it returns a no-op adapter that yields the standard envelope with a note."""
    from dataset_adapters import adapter_for
    ds = Dataset(dataset_id="LLS:ODD", title="Odd dataset", type="lbx.other",
                 filename="WEIRD.lbszzz", location="/x/WEIRD.lbszzz",
                 schema_family="misc")
    out = adapter_for("misc").query({"book": 66, "chapter": 3, "verse_start": 25}, ds)
    assert out["dataset"] == "LLS:ODD"
    assert out["results"] == []
    assert "not yet available" in out["note"]
