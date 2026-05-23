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
