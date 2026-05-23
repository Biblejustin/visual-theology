import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import companion_tools


def test_read_library_section_returns_full_text(monkeypatch):
    captured = {}
    def fake_read(resource_file, article_num, max_chars=8000):
        captured['args'] = (resource_file, article_num, max_chars)
        return "Full verbatim article text from Wallace."
    monkeypatch.setattr(companion_tools, 'read_article_text', fake_read, raising=False)
    out = companion_tools.execute_tool(
        "read_library_section",
        {"filename": "WALLACEGGBB.logos4", "article_num": 4718, "max_chars": 8000},
        {})
    assert out["text"] == "Full verbatim article text from Wallace."
    assert captured['args'] == ("WALLACEGGBB.logos4", 4718, 8000)


def test_read_library_section_handles_missing_text(monkeypatch):
    monkeypatch.setattr(companion_tools, 'read_article_text', lambda *a, **k: None, raising=False)
    out = companion_tools.execute_tool(
        "read_library_section", {"filename": "X.logos4", "article_num": 1}, {})
    assert "error" in out
