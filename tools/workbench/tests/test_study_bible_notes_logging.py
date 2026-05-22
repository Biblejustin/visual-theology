"""Study-bible note lookups must not fail silently.

Bug 2026-05-22: a degraded LogosReader made find_study_bible_notes return
nothing, and the bare `except Exception: return []` in _get_study_bible_notes
turned that into a blank study-bible card with ZERO log signal — it took
process forensics to diagnose. A failure must be logged.
"""
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import app as app_module  # noqa: F401  (sets up sys.path for `study`)
import study as study_module


def test_get_study_bible_notes_logs_on_failure(monkeypatch, caplog):
    def boom(ref, max_chars=5000):
        raise RuntimeError("reader wedged")

    monkeypatch.setattr(app_module, 'find_study_bible_notes', boom)
    with caplog.at_level(logging.ERROR):
        result = app_module._get_study_bible_notes({'passage_ref': 'Romans 3:25-26'})

    # Graceful degradation is preserved (no 500 on the page) ...
    assert result == []
    # ... but the failure is no longer silent.
    assert any('study bible' in r.getMessage().lower() for r in caplog.records), \
        "study-bible lookup failure was swallowed with no log record"


def test_get_study_bible_notes_returns_notes_when_lookup_succeeds(monkeypatch):
    """Regression: the happy path still returns the notes unchanged."""
    notes = [{'abbrev': 'ESV SB', 'title': 'ESV', 'text': 'note'}]
    monkeypatch.setattr(app_module, 'find_study_bible_notes', lambda ref, max_chars=5000: notes)
    result = app_module._get_study_bible_notes({'passage_ref': 'Romans 3:25-26'})
    assert result == notes


def test_find_study_bible_notes_logs_when_a_bible_lookup_raises(monkeypatch, caplog):
    """The ORIGINAL blank-card bug: a wedged reader makes find_commentary_section
    raise per bible, the silent `except: continue` swallows every one, and the
    function returns [] without raising — so the app-level guard never fires.
    Per-bible failures must log."""
    monkeypatch.setattr(study_module.os.path, 'isfile', lambda p: True)

    def boom(fpath, ref):
        raise RuntimeError("reader wedged")

    monkeypatch.setattr(study_module, 'find_commentary_section', boom)
    ref = {'book': 66, 'chapter': 3, 'verse_start': 25, 'verse_end': 26,
           'book_name': 'Romans', 'ref_str': 'Romans 3:25-26'}
    with caplog.at_level(logging.WARNING):
        result = study_module.find_study_bible_notes(ref, max_chars=5000)

    assert result == []
    assert any('study bible' in r.getMessage().lower() for r in caplog.records), \
        "per-bible lookup failure was swallowed silently with no log record"
