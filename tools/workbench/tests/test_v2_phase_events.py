"""Tests for v2-phase event firing on picker changes (review #4 follow-up).

Before this fix, the v2 picker updated the v2_phase column but never
fired phase_events. The persona prompt's ACTIVE FOCUS block read elapsed
from phase_events keyed by the legacy current_phase, so picker changes
showed the wrong phase's elapsed time (or zero).

Now: POST /study/session/<id>/v2-phase fires exit(old_v2) + enter(new_v2)
events so get_phase_time_seconds(v2_key) returns accurate per-phase time.
"""

import os
import sys
import tempfile

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


def _phase_events(analytics, session_id):
    conn = analytics._conn()
    rows = conn.execute(
        "SELECT phase, event_type FROM phase_events WHERE session_id = ? ORDER BY id",
        (session_id,)).fetchall()
    conn.close()
    return [(r['phase'], r['event_type']) for r in rows]


def _new_session(client):
    r = client.post('/study/session/new', data={'passage': 'Romans 1:18-23'})
    return int(r.headers['Location'].rstrip('/').split('/')[-1])


# ── Picker fires v2 enter on first set ────────────────────────────────

def test_first_picker_change_fires_v2_enter(client):
    import app as app_module
    sid = _new_session(client)
    # Drain the session-creation event so we can see the picker's effect cleanly.
    before = _phase_events(app_module.study_analytics, sid)
    r = client.post(f'/study/session/{sid}/v2-phase',
                    json={'v2_phase': 'p09_systematics'})
    assert r.status_code == 200
    after = _phase_events(app_module.study_analytics, sid)
    new_events = after[len(before):]
    # First v2 set has no old_v2 to exit, so just an enter.
    assert ('p09_systematics', 'enter') in new_events


# ── Subsequent change fires exit-old + enter-new ─────────────────────

def test_second_picker_change_fires_exit_and_enter(client):
    import app as app_module
    sid = _new_session(client)
    # First set: just an enter.
    client.post(f'/study/session/{sid}/v2-phase',
                json={'v2_phase': 'p06_context'})
    before = _phase_events(app_module.study_analytics, sid)
    # Second set: should fire exit-old + enter-new.
    client.post(f'/study/session/{sid}/v2-phase',
                json={'v2_phase': 'p12_commentaries'})
    new_events = _phase_events(app_module.study_analytics, sid)[len(before):]
    assert ('p06_context', 'exit') in new_events
    assert ('p12_commentaries', 'enter') in new_events


# ── No-op transition doesn't pollute the log ─────────────────────────

def test_picker_resubmit_same_key_is_noop(client):
    import app as app_module
    sid = _new_session(client)
    client.post(f'/study/session/{sid}/v2-phase',
                json={'v2_phase': 'p06_context'})
    before = _phase_events(app_module.study_analytics, sid)
    # Re-submit the same key.
    client.post(f'/study/session/{sid}/v2-phase',
                json={'v2_phase': 'p06_context'})
    after = _phase_events(app_module.study_analytics, sid)
    # No new events from the no-op.
    assert after == before


# ── get_phase_time_seconds works with v2 keys ────────────────────────

def test_get_phase_time_seconds_returns_data_for_v2_key(client):
    """Smoke check: after firing v2 events, the elapsed lookup the
    persona prompt uses for ACTIVE FOCUS time gauge returns >= 0."""
    import app as app_module
    sid = _new_session(client)
    client.post(f'/study/session/{sid}/v2-phase',
                json={'v2_phase': 'p06_context'})
    # No exit event yet (still in p06), so elapsed should be 0 — but the
    # query path should not raise.
    elapsed = app_module.study_analytics.get_phase_time_seconds(sid, 'p06_context')
    assert elapsed >= 0


def test_v2_phase_elapsed_accumulates_across_transitions(client):
    """After exit-old + enter-new, the old phase's elapsed should be > 0."""
    import app as app_module
    import time
    sid = _new_session(client)
    client.post(f'/study/session/{sid}/v2-phase',
                json={'v2_phase': 'p06_context'})
    time.sleep(0.05)  # accumulate a measurable amount
    client.post(f'/study/session/{sid}/v2-phase',
                json={'v2_phase': 'p12_commentaries'})
    elapsed = app_module.study_analytics.get_phase_time_seconds(sid, 'p06_context')
    # Should be > 0 since we have a complete enter→exit pair.
    assert elapsed > 0
