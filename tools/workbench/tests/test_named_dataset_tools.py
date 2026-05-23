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
