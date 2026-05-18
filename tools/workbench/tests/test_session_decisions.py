"""Tests for session_decisions — committed-decision tracking per session."""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from session_decisions import (
    SessionDecisionStore, DECISION_KEYS,
)


@pytest.fixture
def store():
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    s = SessionDecisionStore(tmp.name)
    s.init_db()
    yield s
    os.unlink(tmp.name)


def test_init_db_creates_table(store):
    conn = store._conn()
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='session_decisions'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1


def test_decision_keys_cover_phase_13_and_target_length():
    expected = {
        'subject', 'theme', 'ep_otm', 'transition',
        'exegetical_outline', 'fcf', 'hp_tht', 'purpose_statement',
        'target_length_min',
    }
    assert set(DECISION_KEYS) == expected


def test_set_decision_persists(store):
    store.set_decision(session_id=1, key='subject', value='The wrath of God on suppressed truth')
    out = store.list_decisions(session_id=1)
    assert len(out) == 1
    assert out[0]['key'] == 'subject'
    assert out[0]['value'] == 'The wrath of God on suppressed truth'


def test_set_decision_upserts(store):
    store.set_decision(session_id=1, key='subject', value='first cut')
    store.set_decision(session_id=1, key='subject', value='sharper cut')
    out = store.list_decisions(session_id=1)
    assert len(out) == 1
    assert out[0]['value'] == 'sharper cut'


def test_set_decision_rejects_unknown_key(store):
    with pytest.raises(ValueError):
        store.set_decision(session_id=1, key='bogus_key', value='x')


def test_list_decisions_filters_by_session(store):
    store.set_decision(session_id=1, key='subject', value='session 1')
    store.set_decision(session_id=2, key='subject', value='session 2')
    out = store.list_decisions(session_id=2)
    assert len(out) == 1
    assert out[0]['value'] == 'session 2'


def test_get_decision_returns_value(store):
    store.set_decision(session_id=1, key='fcf', value='Self-righteousness')
    assert store.get_decision(1, 'fcf') == 'Self-righteousness'


def test_get_decision_returns_none_when_unset(store):
    assert store.get_decision(1, 'fcf') is None


def test_clear_decision_removes_row(store):
    store.set_decision(session_id=1, key='subject', value='x')
    store.clear_decision(1, 'subject')
    assert store.get_decision(1, 'subject') is None


def test_open_decision_keys_returns_unset_only(store):
    store.set_decision(session_id=1, key='subject', value='set')
    store.set_decision(session_id=1, key='theme', value='set')
    open_keys = store.open_decision_keys(1)
    assert 'subject' not in open_keys
    assert 'theme' not in open_keys
    assert 'fcf' in open_keys
    assert 'hp_tht' in open_keys


def test_open_decision_keys_all_open_when_nothing_set(store):
    open_keys = store.open_decision_keys(1)
    assert set(open_keys) == set(DECISION_KEYS)


def test_format_decision_summary_lists_committed_and_open(store):
    store.set_decision(session_id=1, key='subject', value='The wrath of God')
    store.set_decision(session_id=1, key='theme',
                       value='God reveals His wrath against suppression of truth')
    out = store.format_decision_summary(1)
    assert 'subject' in out.lower()
    assert 'The wrath of God' in out
    assert 'theme' in out.lower()
    # mentions open / not-yet decisions too
    assert 'fcf' in out.lower() or 'open' in out.lower() or 'not yet' in out.lower()


def test_format_decision_summary_empty_session_says_none_committed(store):
    out = store.format_decision_summary(1)
    assert 'none' in out.lower() or 'no decisions' in out.lower() or 'all open' in out.lower()
