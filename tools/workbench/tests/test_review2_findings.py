"""Regression tests for the 2026-05-18 second review.

Pins:
  #1 — HTMX is loaded on /study/* pages (was missing → hx-* attrs silently
       no-op'd on set/clear/dismiss buttons).
  #2 — Card next/back transitions record phase_events so the Time Coach
       phase budget + hyperfocus detection actually have data.
  #5 — select_persona_prompt accepts every kwarg agent_data_provider can
       emit (decision_summary, recent_positive_feedback,
       target_length_committed). Was a latent TypeError on
       composing/editing when those stores returned data.
  #6 — chat_markdown filter renders **bold**, *italic*, `code`, lists.
"""

import inspect
import os
import re
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ── #1 HTMX loaded on /study/* templates ─────────────────────────────

STUDY_TEMPLATES = [
    'study_session.html',
    'study_start.html',
    'study_patterns.html',
    'study_feedback_new.html',
]


@pytest.mark.parametrize('template', STUDY_TEMPLATES)
def test_study_template_loads_htmx(template):
    """Regression: /study/* templates omitted htmx.min.js so hx-* attrs
    on cutoff dismiss, decisions panel set/clear silently no-op'd."""
    path = os.path.join(os.path.dirname(__file__), '..', 'templates', template)
    with open(path) as f:
        html = f.read()
    assert '/static/htmx.min.js' in html, \
        f'{template} missing htmx — hx-* attrs in its partials will no-op'


# ── #2 Phase events recorded on card transitions ─────────────────────

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


def _phase_events(analytics, session_id):
    conn = analytics._conn()
    rows = conn.execute(
        "SELECT phase, event_type FROM phase_events WHERE session_id = ? ORDER BY id",
        (session_id,)).fetchall()
    conn.close()
    return [(r['phase'], r['event_type']) for r in rows]


def test_card_next_records_exit_and_enter(client):
    """Regression: update_phase used to fire without phase_events, so the
    Time Coach phase ring stayed at full 45m forever."""
    import app as app_module
    r = client.post('/study/session/new', data={'passage': 'Romans 1:18-23'})
    sid = int(r.headers['Location'].rstrip('/').split('/')[-1])

    # New session records prayer enter only.
    events = _phase_events(app_module.study_analytics, sid)
    assert events == [('prayer', 'enter')]

    # Advance via /card/next.
    client.post(f'/study/session/{sid}/card/next', data={'response': 'amen'})
    events = _phase_events(app_module.study_analytics, sid)
    # prayer enter → prayer exit → read_translate enter (or whatever
    # phase 2 is named — assert the SHAPE, not the literal label).
    assert events[0] == ('prayer', 'enter')
    assert events[1] == ('prayer', 'exit')
    assert events[2][1] == 'enter'
    assert events[2][0] != 'prayer'


def test_card_back_records_exit_and_enter(client):
    import app as app_module
    r = client.post('/study/session/new', data={'passage': 'Romans 1:18-23'})
    sid = int(r.headers['Location'].rstrip('/').split('/')[-1])
    # Advance once, then back.
    client.post(f'/study/session/{sid}/card/next', data={'response': 'amen'})
    client.post(f'/study/session/{sid}/card/back')
    events = _phase_events(app_module.study_analytics, sid)
    # The 'back' step must produce an exit from the current phase and a
    # re-enter into the previous one.
    exits = [e for e in events if e[1] == 'exit']
    enters = [e for e in events if e[1] == 'enter']
    assert len(exits) >= 2, f'expected ≥2 exit events, got {events}'
    assert len(enters) >= 2


# ── #5 select_persona_prompt accepts all data-layer kwargs ───────────

DATA_LAYER_KWARGS = {
    # composing persona kwargs
    'prior_phase_summary',
    'sermon_coach_pattern_summary',
    'decision_summary',
    # editing persona kwargs
    'manuscript',
    'estimated_minutes',
    'active_commitment',
    'recent_coach_one_changes',
    'prior_overruns_summary',
    'recent_positive_feedback',
    'target_length_committed',
}


def test_select_persona_prompt_accepts_every_data_layer_kwarg():
    """Regression: agent_data_provider returned keys
    (decision_summary, recent_positive_feedback, target_length_committed)
    that select_persona_prompt didn't accept — composing/editing prompt
    construction would TypeError when those stores had data."""
    from companion_agent import select_persona_prompt
    sig = inspect.signature(select_persona_prompt)
    declared = set(sig.parameters.keys())
    missing = DATA_LAYER_KWARGS - declared
    assert not missing, \
        f'select_persona_prompt missing kwargs from agent_data_provider: {missing}'


def test_composing_prompt_with_decision_summary_does_not_crash():
    """End-to-end: data-layer dict unpacks cleanly into the composing
    prompt builder with a real decision_summary payload."""
    from companion_agent import select_persona_prompt
    p = select_persona_prompt(
        persona='composing', passage='Rom 1', genre='epistle',
        session_elapsed_seconds=300, current_phase='exegetical_point',
        outline_summary='', card_work_summary='', coaching_context='',
        prior_phase_summary='', sermon_coach_pattern_summary='',
        decision_summary='Committed: subject=grace, ep_otm=salvation by faith',
    )
    assert 'grace' in p or 'subject' in p.lower() or len(p) > 100


def test_editing_prompt_with_all_extra_context_does_not_crash():
    from companion_agent import select_persona_prompt
    p = select_persona_prompt(
        persona='editing', passage='Rom 1', genre='epistle',
        session_elapsed_seconds=300, current_phase='edit_pray',
        outline_summary='', card_work_summary='', coaching_context='',
        manuscript='draft text here', estimated_minutes=32,
        active_commitment='Pause at applications',
        recent_coach_one_changes='Cut redundant illustrations',
        prior_overruns_summary='avg +12.4 min',
        recent_positive_feedback='Olivia said the gospel landed',
        target_length_committed=25,
    )
    assert 'draft text here' in p
    # Length gauge should reflect committed target (25, not 28).
    assert '25 min' in p or 'committed target' in p.lower()


# ── #6 chat_markdown filter ──────────────────────────────────────────

def test_chat_markdown_bold():
    from app import chat_markdown_filter
    out = str(chat_markdown_filter('**Excellent work, Bryan.**'))
    assert '<strong>Excellent work, Bryan.</strong>' in out


def test_chat_markdown_italic():
    from app import chat_markdown_filter
    out = str(chat_markdown_filter('this is *important*'))
    assert '<em>important</em>' in out


def test_chat_markdown_code():
    from app import chat_markdown_filter
    out = str(chat_markdown_filter('use `lookup_lexicon` for that'))
    assert '<code>lookup_lexicon</code>' in out


def test_chat_markdown_bullet_list():
    from app import chat_markdown_filter
    out = str(chat_markdown_filter('- first\n- second\n- third'))
    assert '<ul>' in out and '<li>first</li>' in out and '<li>third</li>' in out


def test_chat_markdown_numbered_list():
    from app import chat_markdown_filter
    out = str(chat_markdown_filter('1. one\n2. two'))
    assert '<ol>' in out and '<li>one</li>' in out and '<li>two</li>' in out


def test_chat_markdown_paragraphs():
    from app import chat_markdown_filter
    out = str(chat_markdown_filter('first para\n\nsecond para'))
    assert out.count('<p>') == 2


def test_chat_markdown_escapes_html():
    """Injection safety: escape THEN format, never the other way."""
    from app import chat_markdown_filter
    out = str(chat_markdown_filter('<script>alert(1)</script>'))
    assert '<script>' not in out
    assert '&lt;script&gt;' in out


def test_chat_markdown_empty_string():
    from app import chat_markdown_filter
    assert chat_markdown_filter('') == ''
    assert chat_markdown_filter(None) == ''
