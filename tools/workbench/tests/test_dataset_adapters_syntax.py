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


def test_syntax_adapter_deep_seam_handles_no_reader(monkeypatch):
    """Deeper-seam: if dataset_tools._get_reader() returns None, the real
    _digest_phrases body must safely return [] (so the envelope reports
    'no matches' rather than crashing)."""
    import dataset_tools
    monkeypatch.setattr(dataset_tools, "_get_reader", lambda: None, raising=False)
    out = adapter_for("syntax").query(
        {"book": 66, "chapter": 3, "verse_start": 22}, _ds())
    assert out["results"] == []
