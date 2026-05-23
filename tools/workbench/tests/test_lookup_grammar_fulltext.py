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
