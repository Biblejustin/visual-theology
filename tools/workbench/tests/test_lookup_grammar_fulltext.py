import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import companion_tools


class _FakeSearcher:
    def search(self, query, **kw):
        # One hit in a grammar resource (filename matches a known grammar).
        gram_file = companion_tools.NT_GRAMMARS[0]["file"]
        return {"ok": True, "results": [{
            "resource_id": "LLS:GRAMMAR", "filename": gram_file,
            "title": "Greek Grammar Beyond the Basics", "abbrev": "Wallace",
            "sections": [{"article_num": 1234, "snippet": "…the objective genitive…"}],
        }]}


def test_lookup_grammar_uses_fulltext_and_returns_section(monkeypatch):
    monkeypatch.setattr(companion_tools, 'get_searcher', lambda: _FakeSearcher(), raising=False)
    monkeypatch.setattr(companion_tools, 'read_article_text',
                        lambda f, a, max_chars=8000: "Wallace's full section on the objective genitive.",
                        raising=False)
    out = companion_tools._lookup_grammar({"query": "subjective vs objective genitive", "testament": "nt"}, {})
    entries = out["entries"]
    assert entries, "expected a grammar section"
    assert "objective genitive" in entries[0]["text"].lower()
    assert entries[0]["abbrev"]  # carries the source abbrev for citation


def test_lookup_grammar_skips_hit_when_reader_raises_and_logs(monkeypatch, caplog):
    """A reader failure on one hit must not abort the whole tool call."""
    import logging
    gram_file = companion_tools.NT_GRAMMARS[0]["file"]
    other_file = companion_tools.NT_GRAMMARS[1]["file"] if len(companion_tools.NT_GRAMMARS) > 1 else gram_file
    class _Searcher:
        def search(self, query, **kw):
            return {"ok": True, "results": [
                {"resource_id": "A", "filename": gram_file, "title": "G1", "abbrev": "g1",
                 "sections": [{"article_num": 1, "snippet": "x"}]},
                {"resource_id": "B", "filename": other_file, "title": "G2", "abbrev": "g2",
                 "sections": [{"article_num": 2, "snippet": "y"}]},
            ]}
    calls = {"n": 0}
    def flaky(filename, art, max_chars=6000):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("reader wedged")
        return "Second section text."
    monkeypatch.setattr(companion_tools, 'get_searcher', lambda: _Searcher(), raising=False)
    monkeypatch.setattr(companion_tools, 'read_article_text', flaky, raising=False)
    with caplog.at_level(logging.WARNING):
        out = companion_tools._lookup_grammar({"query": "x", "testament": "nt"}, {})
    assert len(out["entries"]) == 1
    assert out["entries"][0]["text"] == "Second section text."
    assert any("grammar" in r.getMessage().lower() or "reader" in r.getMessage().lower()
               for r in caplog.records)


def test_lookup_grammar_propagates_searcher_failure(monkeypatch):
    class _BadSearcher:
        def search(self, query, **kw):
            return {"ok": False, "error": "searcher_down", "message": "subprocess exited"}
    monkeypatch.setattr(companion_tools, 'get_searcher', lambda: _BadSearcher(), raising=False)
    out = companion_tools._lookup_grammar({"query": "x", "testament": "nt"}, {})
    assert "error" in out
    assert out["error"] == "searcher_down"
    assert "entries" not in out or out.get("entries") == []


def test_lookup_grammar_tool_description_is_current():
    """Description must reflect the full-text reroute, not the old headword path."""
    tdef = next(t for t in companion_tools.TOOL_DEFINITIONS if t["name"] == "lookup_grammar")
    desc = tdef["description"].lower()
    assert "full text" in desc or "full-text" in desc
    assert "headword" not in desc or "not headword" in desc.replace(",", "")
    # Must still name the grammars so the LLM knows what's available
    assert "wallace" in desc
