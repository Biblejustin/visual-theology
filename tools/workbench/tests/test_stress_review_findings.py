"""Regression tests for the 2026-05-19 stress-test review.

Pins:
  #1 — UI persona display now reads v2_phase (not legacy current_phase)
       so the header matches what the agent actually uses.
  #2 — v2 phase transitions are race-safe. The pre-fix endpoint had a
       SELECT-then-UPDATE-then-INSERT sequence with no transaction
       boundary; 60 concurrent calls produced 7 enters / 5 exits.
       The new transition_v2_phase wraps it in BEGIN IMMEDIATE so
       phase_events stay balanced under load.
  #5 — /study/session/<missing-id>/outline now 404s instead of 200 [].
"""

import os
import sys
import tempfile
import threading

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
        yield c, tmp.name
    os.unlink(tmp.name)


def _new_session(client):
    r = client.post('/study/session/new', data={'passage': 'Romans 1:18-23'})
    return int(r.headers['Location'].rstrip('/').split('/')[-1])


# ── #1 Persona display reads v2 phase ────────────────────────────────

def test_session_view_persona_follows_v2_phase(client):
    """Set v2_phase to p16_ruthless_edit; header persona must be 'editing'
    even though legacy current_phase still reads 'prayer'."""
    c, _ = client
    sid = _new_session(c)
    c.post(f'/study/session/{sid}/v2-phase', json={'v2_phase': 'p16_ruthless_edit'})
    r = c.get(f'/study/session/{sid}')
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    # Persona indicator partial renders 'editing' chip when persona='editing'.
    # Don't pin literal markup — just confirm the right persona name appears.
    assert 'editing' in body.lower(), \
        'persona display should follow v2_phase, not legacy current_phase'


def test_session_view_persona_falls_back_to_legacy_when_v2_unset(client):
    """Sessions that predate the picker (v2_phase is NULL) should still
    get a reasonable persona via the legacy → v2 resolver."""
    c, _ = client
    sid = _new_session(c)
    # Don't set v2_phase. legacy phase is 'prayer' which → silent persona.
    r = c.get(f'/study/session/{sid}')
    assert r.status_code == 200


# ── #2 v2 phase transitions are race-safe ────────────────────────────

def test_transition_v2_phase_atomic_returns_status(client):
    """The new helper returns ('missing'|'no_change'|'changed', old)."""
    c, db_path = client
    import app as app_module
    sid = _new_session(c)

    db = app_module.companion_db
    # First set: v2_phase was NULL → changed, old=None
    status, old = db.transition_v2_phase(sid, 'p06_context')
    assert status == 'changed'
    assert old is None

    # Re-submit same key: no_change
    status, old = db.transition_v2_phase(sid, 'p06_context')
    assert status == 'no_change'
    assert old is None

    # Change to different key: changed, old=p06_context
    status, old = db.transition_v2_phase(sid, 'p12_commentaries')
    assert status == 'changed'
    assert old == 'p06_context'

    # Missing session
    status, old = db.transition_v2_phase(99999, 'p04_exegesis')
    assert status == 'missing'


def test_v2_phase_events_balanced_under_concurrent_load(client):
    """Stress test from the 2026-05-19 review: hammer the DB layer with
    rapid concurrent phase changes. Pre-fix this produced 7 enters / 5
    exits for p06_context. With BEGIN IMMEDIATE serialization,
    phase_events should stay balanced.

    What 'balanced' means: every phase EXCEPT the final one must have
    equal enter and exit counts. The currently-active phase has one
    extra enter (the most recent transition) with no matching exit
    until the next change.

    Tests transition_v2_phase directly because Flask's test client is
    not thread-safe. The DB-layer call is what actually had the race.
    """
    c, db_path = client
    import app as app_module
    sid = _new_session(c)

    keys = ['p04_exegesis', 'p06_context', 'p09_systematics', 'p12_commentaries']
    n_iterations = 30
    errors = []

    # Each thread opens its OWN CompanionDB pointed at the same file so
    # they race through real SQLite connections, the same way two
    # concurrent HTTP workers would.
    def hammer():
        from companion_db import CompanionDB
        db = CompanionDB(db_path)
        for i in range(n_iterations):
            try:
                db.transition_v2_phase(sid, keys[i % len(keys)])
            except Exception as e:
                errors.append(repr(e))

    threads = [threading.Thread(target=hammer) for _ in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()

    assert not errors, f'concurrent calls produced errors: {errors[:5]}'

    # Tally phase_events. Per-phase enters should equal exits for every
    # phase EXCEPT the final one (which has one open enter). Filter to
    # v2 keys (p<num>_*); legacy 'prayer' from session creation is a
    # different log path and is unbalanced by design.
    conn = app_module.study_analytics._conn()
    rows = conn.execute(
        "SELECT phase, event_type, COUNT(*) c FROM phase_events "
        "WHERE session_id = ? AND phase GLOB 'p[0-9][0-9]_*' "
        "GROUP BY phase, event_type",
        (sid,)
    ).fetchall()
    conn.close()

    counts = {}
    for r in rows:
        counts.setdefault(r['phase'], {'enter': 0, 'exit': 0})
        counts[r['phase']][r['event_type']] = r['c']

    # The session's final v2_phase is the only one allowed to have
    # enter > exit (by exactly 1). Every other phase must balance.
    final_phase = app_module.companion_db.get_session(sid)['v2_phase']
    for phase, c2 in counts.items():
        diff = c2['enter'] - c2['exit']
        if phase == final_phase:
            assert diff == 1, \
                f'final phase {phase}: enter={c2["enter"]} exit={c2["exit"]} (expected diff=1)'
        else:
            # Non-final phases must be paired.
            assert diff == 0, \
                f'phase {phase}: enter={c2["enter"]} exit={c2["exit"]} ' \
                f'(unbalanced — concurrency leak)'


# ── #5 Missing-session outline 404s ──────────────────────────────────

def test_missing_session_outline_returns_404(client):
    c, _ = client
    r = c.get('/study/session/999999/outline')
    assert r.status_code == 404


def test_existing_session_outline_returns_200_with_array(client):
    c, _ = client
    sid = _new_session(c)
    r = c.get(f'/study/session/{sid}/outline')
    assert r.status_code == 200
    assert r.get_json() == []
