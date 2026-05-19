"""Tests for the v2 16-phase model (review #4)."""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ── Metadata invariants ──────────────────────────────────────────────

def test_v2_phases_has_exactly_16_phases():
    from v2_phases import V2_PHASES
    assert len(V2_PHASES) == 16


def test_v2_phases_numbered_1_through_16():
    from v2_phases import V2_PHASES
    nums = [p.number for p in V2_PHASES]
    assert nums == list(range(1, 17))


def test_v2_phases_persona_distribution_matches_spec():
    """Per spec §3: phases 1-3 silent, 4-12 research, 13-14 composing, 15-16 editing."""
    from v2_phases import V2_PHASES
    by_persona = {}
    for p in V2_PHASES:
        by_persona.setdefault(p.persona, []).append(p.number)
    assert sorted(by_persona['silent']) == [1, 2, 3]
    assert sorted(by_persona['research']) == [4, 5, 6, 7, 8, 9, 10, 11, 12]
    assert sorted(by_persona['composing']) == [13, 14]
    assert sorted(by_persona['editing']) == [15, 16]


def test_v2_phase_keys_are_unique():
    from v2_phases import V2_PHASES
    keys = [p.key for p in V2_PHASES]
    assert len(keys) == len(set(keys))


# ── Lookups ──────────────────────────────────────────────────────────

def test_get_phase_by_key():
    from v2_phases import get_phase
    p = get_phase('p06_context')
    assert p is not None
    assert p.number == 6
    assert p.persona == 'research'


def test_get_phase_by_number():
    from v2_phases import get_phase
    p = get_phase(13)
    assert p.key == 'p13_sermon_work'
    assert p.persona == 'composing'


def test_get_phase_returns_none_for_unknown():
    from v2_phases import get_phase
    assert get_phase('p99_nonexistent') is None
    assert get_phase(99) is None
    assert get_phase(None) is None


# ── Legacy → v2 mapping ──────────────────────────────────────────────

@pytest.mark.parametrize('legacy,expected_persona', [
    ('prayer',              'silent'),
    ('text_work',           'silent'),
    ('digestion',           'silent'),
    ('observation',         'research'),
    ('study_bibles',        'research'),
    ('context',             'research'),
    ('theological',         'research'),
    ('commentary',          'research'),
    ('exegetical_point',    'composing'),
    ('fcf_homiletical',     'composing'),
    ('sermon_construction', 'composing'),
    ('edit_pray',           'editing'),
    ('study',               'research'),
])
def test_resolve_v2_phase_from_legacy(legacy, expected_persona):
    from v2_phases import resolve_v2_phase
    p = resolve_v2_phase(None, legacy)
    assert p.persona == expected_persona


def test_resolve_v2_phase_explicit_wins_over_legacy():
    """When v2_phase is set, it wins regardless of legacy current_phase."""
    from v2_phases import resolve_v2_phase
    p = resolve_v2_phase('p10_patristics', 'prayer')
    assert p.key == 'p10_patristics'
    assert p.persona == 'research'


def test_resolve_v2_phase_unknown_explicit_falls_through_to_legacy():
    """Stale picker value shouldn't break anything — fall back to legacy."""
    from v2_phases import resolve_v2_phase
    p = resolve_v2_phase('p99_stale', 'edit_pray')
    assert p.key == 'p16_ruthless_edit'


def test_resolve_v2_phase_no_signal_defaults_to_exegesis():
    from v2_phases import resolve_v2_phase
    p = resolve_v2_phase(None, None)
    assert p.key == 'p04_exegesis'


# ── Router integration ──────────────────────────────────────────────

@pytest.mark.parametrize('key,expected', [
    ('p01_prayer',          'silent'),
    ('p03_digestion',       'silent'),
    ('p04_exegesis',        'research'),
    ('p12_commentaries',    'research'),
    ('p13_sermon_work',     'composing'),
    ('p14_sermon_writing',  'composing'),
    ('p15_finishing',       'editing'),
    ('p16_ruthless_edit',   'editing'),
])
def test_persona_for_v2_phase(key, expected):
    from phase_cluster_router import persona_for_v2_phase
    assert persona_for_v2_phase(key) == expected


def test_persona_for_v2_phase_unknown_defaults_to_research():
    from phase_cluster_router import persona_for_v2_phase
    assert persona_for_v2_phase('p99_nonexistent') == 'research'


def test_persona_for_v2_phase_empty_defaults_to_research():
    from phase_cluster_router import persona_for_v2_phase
    assert persona_for_v2_phase('') == 'research'
    assert persona_for_v2_phase(None) == 'research'


# ── DB column + helper ───────────────────────────────────────────────

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


def test_v2_phase_column_exists(db):
    conn = db._conn()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    conn.close()
    assert 'v2_phase' in cols


def test_update_v2_phase_sets_value(db):
    sid = db.create_session('Romans 1', 45, 1, 1, 1, 'epistle')
    affected = db.update_v2_phase(sid, 'p07_big_picture')
    assert affected == 1
    sess = db.get_session(sid)
    assert sess['v2_phase'] == 'p07_big_picture'


def test_update_v2_phase_returns_zero_for_missing_session(db):
    """Missing session → 0 rows affected, no crash."""
    assert db.update_v2_phase(99999, 'p04_exegesis') == 0


def test_new_session_has_null_v2_phase(db):
    """Existing sessions and freshly-created ones default to NULL.
    The picker shows the legacy-mapped default; setting writes the column."""
    sid = db.create_session('Romans 1', 45, 1, 1, 1, 'epistle')
    sess = db.get_session(sid)
    assert sess['v2_phase'] is None


# ── HTTP endpoint ────────────────────────────────────────────────────

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


def _new_session(client):
    r = client.post('/study/session/new', data={'passage': 'Romans 1:18-23'})
    return int(r.headers['Location'].rstrip('/').split('/')[-1])


def test_set_v2_phase_endpoint_happy_path(client):
    import app as app_module
    sid = _new_session(client)
    r = client.post(f'/study/session/{sid}/v2-phase',
                    json={'v2_phase': 'p09_systematics'})
    assert r.status_code == 200
    data = r.get_json()
    assert data['v2_phase'] == 'p09_systematics'
    assert data['persona'] == 'research'
    # Persisted
    sess = app_module.companion_db.get_session(sid)
    assert sess['v2_phase'] == 'p09_systematics'


def test_set_v2_phase_rejects_unknown_key(client):
    sid = _new_session(client)
    r = client.post(f'/study/session/{sid}/v2-phase',
                    json={'v2_phase': 'p99_stale'})
    assert r.status_code == 400


def test_set_v2_phase_rejects_missing_session(client):
    r = client.post('/study/session/99999/v2-phase',
                    json={'v2_phase': 'p04_exegesis'})
    assert r.status_code == 404


def test_set_v2_phase_rejects_empty_body(client):
    sid = _new_session(client)
    r = client.post(f'/study/session/{sid}/v2-phase', json={})
    assert r.status_code == 400
