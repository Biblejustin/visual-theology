"""Tests for the resume card on /study/ (build pick #1) + mobile sticky
chat input (build pick #3)."""

import os
import sys
import tempfile
import time as time_mod

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture
def client():
    import app as app_module
    from conftest import init_all_app_globals
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    init_all_app_globals(tmp.name)
    app_module.app.config['TESTING'] = True
    with app_module.app.test_client() as c:
        yield c
    os.unlink(tmp.name)


def _new_session(client, passage='Romans 1:18-23'):
    r = client.post('/study/session/new', data={'passage': passage})
    return int(r.headers['Location'].rstrip('/').split('/')[-1])


# ── #1 Resume card ───────────────────────────────────────────────────

def test_resume_card_omitted_when_no_sessions(client):
    """Empty state: no resume card, just the new-passage form."""
    r = client.get('/study/')
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert 'resume-card' not in body
    # Existing UI still intact
    assert 'What are you preaching?' in body


def test_resume_card_surfaces_most_recent_session(client):
    """When sessions exist, the top of /study/ leads with Resume X."""
    _new_session(client, 'Genesis 1:1-5')
    time_mod.sleep(0.05)
    sid2 = _new_session(client, 'Romans 8:1-11')
    r = client.get('/study/')
    body = r.get_data(as_text=True)
    assert 'class="resume-card"' in body
    # Most recent (Romans 8:1-11) is what gets surfaced.
    assert 'Romans 8:1-11' in body
    # Link points to that session.
    assert f'/study/session/{sid2}' in body


def test_resume_card_shows_cached_next_action_when_present(client):
    """The card's most valuable surface is the next-action sentence."""
    import app as app_module
    sid = _new_session(client)
    app_module.companion_db.set_next_action(
        sid, 'Surface one geography blind spot.', 'p06_context')
    app_module.companion_db.update_v2_phase(sid, 'p06_context')
    r = client.get('/study/')
    body = r.get_data(as_text=True)
    assert 'Surface one geography blind spot.' in body
    assert 'resume-card-next' in body


def test_resume_card_omits_next_when_no_cache(client):
    """No next_action_text → render the card without the Next line
    (don't show empty 'Next:' label)."""
    _new_session(client)
    r = client.get('/study/')
    body = r.get_data(as_text=True)
    assert 'class="resume-card"' in body
    assert 'resume-card-next' not in body


def test_resume_card_no_persona_chip(client):
    """The persona chip ('research'/'composing'/'editing') used to leak
    internal architecture onto the resume card. Bryan doesn't need to
    see which persona is on duty — the AI just acts right for the phase.
    Make sure none of the persona class names render."""
    import app as app_module
    sid = _new_session(client)
    app_module.companion_db.update_v2_phase(sid, 'p16_ruthless_edit')
    r = client.get('/study/')
    body = r.get_data(as_text=True)
    for chip in ('persona-research', 'persona-composing',
                 'persona-editing', 'persona-silent', 'resume-persona'):
        assert chip not in body, f"resume card still leaks {chip!r}"


def test_resume_card_surfaces_most_recent_child_when_active(client):
    """Children are first-class preaching units. When a child is the
    most-recently touched session (which is the typical week-of state
    for series prep), the resume card surfaces THAT child, not the
    less-recently-touched series parent."""
    import app as app_module
    parent_sid = _new_session(client, 'Romans 1:1-32')
    # Synthesize a child session pointing at the parent, touched LATER.
    conn = app_module.companion_db._conn()
    cur = conn.execute(
        "INSERT INTO sessions (passage_ref, book, chapter, verse_start, verse_end, "
        "genre, current_phase, timer_remaining_seconds, status, parent_session_id, "
        "created_at, updated_at) "
        "VALUES ('Romans 1:1-7', 45, 1, 1, 7, 'epistle', 'prayer', 900, 'active', "
        "?, datetime('now', '+1 minute'), datetime('now', '+1 minute'))",
        (parent_sid,))
    child_sid = cur.lastrowid
    conn.commit()
    conn.close()
    r = client.get('/study/')
    body = r.get_data(as_text=True)
    # The newer child wins.
    assert 'Romans 1:1-7' in body
    assert f'/study/session/{child_sid}' in body


# ── _humanize_since ──────────────────────────────────────────────────

def test_humanize_since_handles_minutes():
    from app import _humanize_since
    from datetime import datetime, timezone, timedelta
    ts = (datetime.now(timezone.utc) - timedelta(minutes=18)).isoformat()
    assert _humanize_since(ts) == '18 minutes ago'


def test_humanize_since_handles_singular_hour():
    from app import _humanize_since
    from datetime import datetime, timezone, timedelta
    ts = (datetime.now(timezone.utc) - timedelta(hours=1, minutes=2)).isoformat()
    assert _humanize_since(ts) == '1 hour ago'


def test_humanize_since_just_now_under_a_minute():
    from app import _humanize_since
    from datetime import datetime, timezone, timedelta
    ts = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    assert _humanize_since(ts) == 'just now'


def test_humanize_since_returns_empty_for_garbage():
    from app import _humanize_since
    assert _humanize_since('') == ''
    assert _humanize_since(None) == ''
    assert _humanize_since('not-an-iso') == ''


# ── #3 Mobile sticky chat input (CSS contract) ───────────────────────

def test_mobile_input_area_is_sticky():
    """The mobile breakpoint must make #input-area sticky to the bottom
    so dictation + send stay reachable on long threads. Pure CSS
    contract check — assertions on selectors + properties, not visual
    layout (which needs a browser).

    There are multiple @media (max-width: 700px) blocks in study.css
    (one is the resume-card spacing tweak); search them all for the
    rule that targets #input-area."""
    here = os.path.dirname(os.path.abspath(__file__))
    css = open(os.path.join(here, '..', 'static', 'study.css')).read()
    pos = 0
    found_sticky = False
    while True:
        start = css.find('@media (max-width: 700px)', pos)
        if start < 0:
            break
        block = css[start:start + 8000]
        if '#input-area' in block:
            idx = block.find('#input-area')
            rule = block[idx:idx + 400]
            if ('position: sticky' in rule
                    and 'bottom: 0' in rule
                    and 'env(safe-area-inset-bottom)' in rule):
                found_sticky = True
                break
        pos = start + 1
    assert found_sticky, \
        'mobile #input-area should be position:sticky + bottom:0 + ' \
        'env(safe-area-inset-bottom) (build pick #3)'
