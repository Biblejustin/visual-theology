"""Tests for the next-action EF-prosthesis cue (build pick #2).

Covers:
  - generate_next_action returns sanitized text from a stub LLM
  - silent phases (1-3) skip generation
  - unknown phase returns None
  - DB setter persists text + phase
  - phase transition triggers regeneration; no-op resubmit doesn't
  - manual refresh endpoint
  - session render passes next_action_text + active_focus_posture
  - partial template surfaces the text
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ── Stub LLM (mirrors llm_client.CannedLLMClient shape) ──────────────

class _StubClient:
    """Yields a fixed text_delta then message_complete, matching
    llm_client.AnthropicClient.stream_message contract."""
    def __init__(self, text):
        self.text = text

    def stream_message(self, system, messages, tools, max_tokens=4096):
        yield {'type': 'text_delta', 'text': self.text}
        yield {'type': 'message_complete', 'usage': {}, 'stop_reason': 'end_turn', 'content': []}


class _ErrorClient:
    def stream_message(self, system, messages, tools, max_tokens=4096):
        yield {'type': 'error', 'error': 'boom'}


# ── Generation ───────────────────────────────────────────────────────

def test_generate_returns_sanitized_text():
    from next_action import generate_next_action
    out = generate_next_action(
        passage='Romans 1:18-23', genre='epistle',
        v2_phase_key='p06_context',
        llm_client=_StubClient('  "Next: Surface one geography blind spot."  '),
    )
    # Sanitizer strips surrounding quotes + 'Next:' prefix + whitespace.
    assert out == 'Surface one geography blind spot.'


def test_generate_caps_extremely_long_output():
    from next_action import generate_next_action
    long_text = 'word ' * 100
    out = generate_next_action(
        passage='Rom 1', genre='epistle',
        v2_phase_key='p06_context',
        llm_client=_StubClient(long_text),
    )
    assert len(out) <= 201


def test_generate_silent_phase_returns_none():
    """Phases 1-3 (silent persona) have no LLM presence — don't burn
    Haiku tokens on a cue that won't render."""
    from next_action import generate_next_action
    out = generate_next_action(
        passage='Rom 1', genre='epistle',
        v2_phase_key='p01_prayer',
        llm_client=_StubClient('should not appear'),
    )
    assert out is None


def test_generate_unknown_phase_returns_none():
    from next_action import generate_next_action
    out = generate_next_action(
        passage='Rom 1', genre='epistle',
        v2_phase_key='p99_nonexistent',
        llm_client=_StubClient('whatever'),
    )
    assert out is None


def test_generate_handles_llm_error_silently():
    from next_action import generate_next_action
    out = generate_next_action(
        passage='Rom 1', genre='epistle',
        v2_phase_key='p06_context',
        llm_client=_ErrorClient(),
    )
    assert out is None


def test_generate_strips_common_prefixes():
    from next_action import generate_next_action
    for raw in ('Next: do the thing.',
                'Next action: do the thing.',
                'Action: do the thing.'):
        out = generate_next_action(
            passage='Rom 1', genre='epistle',
            v2_phase_key='p06_context',
            llm_client=_StubClient(raw))
        assert out == 'do the thing.'


# ── DB setter ────────────────────────────────────────────────────────

@pytest.fixture
def db():
    from companion_db import CompanionDB
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    d = CompanionDB(path)
    d.init_db()
    yield d
    for suffix in ('', '-shm', '-wal'):
        try:
            os.unlink(path + suffix)
        except FileNotFoundError:
            pass


def test_set_next_action_persists_text_and_phase(db):
    sid = db.create_session('Rom 1', 45, 1, 1, 23, 'epistle')
    affected = db.set_next_action(sid, 'Pick the strongest lexicon hit.', 'p04_exegesis')
    assert affected == 1
    sess = db.get_session(sid)
    assert sess['next_action_text'] == 'Pick the strongest lexicon hit.'
    assert sess['next_action_set_for_v2_phase'] == 'p04_exegesis'


def test_set_next_action_missing_session_returns_zero(db):
    assert db.set_next_action(99999, 'whatever', 'p04_exegesis') == 0


# ── Endpoint: refresh ────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    import app as app_module
    from conftest import init_all_app_globals
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    init_all_app_globals(tmp.name)
    app_module.app.config['TESTING'] = True
    # Make _maybe_generate_next_action use the stub so the test doesn't
    # need a live API key.
    import next_action
    monkeypatch.setattr(
        next_action, 'generate_next_action',
        lambda *, passage, genre, v2_phase_key,
                card_work_summary='', outline_summary='', llm_client=None:
            f'STUB cue for {v2_phase_key}'
    )
    with app_module.app.test_client() as c:
        yield c
    os.unlink(tmp.name)


def _new_session(client):
    r = client.post('/study/session/new', data={'passage': 'Romans 1:18-23'})
    return int(r.headers['Location'].rstrip('/').split('/')[-1])


def test_v2_phase_change_caches_next_action(client):
    import app as app_module
    sid = _new_session(client)
    r = client.post(f'/study/session/{sid}/v2-phase',
                    json={'v2_phase': 'p06_context'})
    assert r.status_code == 200
    sess = app_module.companion_db.get_session(sid)
    assert sess['next_action_text'] == 'STUB cue for p06_context'
    assert sess['next_action_set_for_v2_phase'] == 'p06_context'


def test_v2_phase_no_op_does_not_regenerate(client, monkeypatch):
    """Re-submitting the same phase shouldn't fire another LLM call."""
    import app as app_module
    sid = _new_session(client)
    client.post(f'/study/session/{sid}/v2-phase', json={'v2_phase': 'p06_context'})

    calls = []
    import next_action
    monkeypatch.setattr(next_action, 'generate_next_action',
                        lambda **kw: calls.append(1) or 'should not fire')

    # Re-submit same phase.
    client.post(f'/study/session/{sid}/v2-phase', json={'v2_phase': 'p06_context'})
    assert calls == [], 'no-op transition should not regenerate next_action'


def test_refresh_endpoint_forces_regeneration(client):
    sid = _new_session(client)
    client.post(f'/study/session/{sid}/v2-phase', json={'v2_phase': 'p07_big_picture'})
    r = client.post(f'/study/session/{sid}/next-action/refresh')
    assert r.status_code == 200
    assert 'STUB cue for p07_big_picture' in r.get_json()['next_action']


def test_refresh_endpoint_404s_for_missing_session(client):
    r = client.post('/study/session/99999/next-action/refresh')
    assert r.status_code == 404


# ── Session page renders the partial ─────────────────────────────────

def test_session_page_renders_next_action_when_cached(client):
    """Next-action line lives in the conversation column (phases 6+).
    Advance legacy phase past card phases so we see the conversation
    render path."""
    import app as app_module
    sid = _new_session(client)
    app_module.companion_db.update_phase(sid, 'context')  # leave card mode
    app_module.companion_db.set_next_action(
        sid, 'Surface one geography blind spot.', 'p06_context')
    app_module.companion_db.update_v2_phase(sid, 'p06_context')
    r = client.get(f'/study/session/{sid}')
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert 'Surface one geography blind spot.' in body
    assert 'next-action-line' in body


def test_session_page_hides_next_action_when_no_cache(client):
    """When next_action_text is empty, the line should NOT render.
    Previously fell back to persona_prompts._ACTIVE_FOCUS cues which
    are uppercase LLM-facing instructions ("PROACTIVE.", "PROACTIVELY")
    — not user-facing copy. Hide the line entirely instead of leaking
    internal LLM directives onto Bryan's screen."""
    import app as app_module
    sid = _new_session(client)
    app_module.companion_db.update_phase(sid, 'context')
    app_module.companion_db.update_v2_phase(sid, 'p06_context')
    r = client.get(f'/study/session/{sid}')
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert 'PROACTIVE.' not in body
    assert 'PROACTIVELY' not in body
    assert 'class="next-action-line"' not in body
