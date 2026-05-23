import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import companion_tools
from dataset_registry import Dataset


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
