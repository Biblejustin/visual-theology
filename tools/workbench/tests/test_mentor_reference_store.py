"""Tests for the mentor reference store (Cycle 2c).

Substrate only — table + helpers + a single seed example. No active wiring yet.
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from mentor_reference_store import MentorReferenceStore


@pytest.fixture
def store():
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    s = MentorReferenceStore(tmp.name)
    s.init_db()
    yield s
    os.unlink(tmp.name)


def test_init_db_creates_table(store):
    conn = store._conn()
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='mentor_references'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1


def test_add_reference_returns_id(store):
    rid = store.add_reference(
        mentor_key='prutow',
        source_title='So Pastor, What\'s Your Point?',
        topic='main_point',
        position_text='A sermon should have one main point.',
        citation='Ch. 1',
    )
    assert rid > 0


def test_add_reference_defaults_unverified(store):
    rid = store.add_reference(
        mentor_key='prutow', source_title='x', topic='y',
        position_text='z', citation='c')
    refs = store.get_references()
    assert refs[0]['verified'] == 0


def test_add_reference_accepts_verified_true(store):
    rid = store.add_reference(
        mentor_key='prutow', source_title='x', topic='y',
        position_text='z', citation='c', verified=True)
    refs = store.get_references()
    assert refs[0]['verified'] == 1


def test_get_references_filters_by_mentor(store):
    store.add_reference(mentor_key='prutow', source_title='a', topic='t',
                        position_text='p', citation='c')
    store.add_reference(mentor_key='chapell', source_title='b', topic='t',
                        position_text='q', citation='c')
    prutow = store.get_references(mentor_key='prutow')
    chapell = store.get_references(mentor_key='chapell')
    assert len(prutow) == 1 and prutow[0]['mentor_key'] == 'prutow'
    assert len(chapell) == 1 and chapell[0]['mentor_key'] == 'chapell'


def test_get_references_filters_by_topic(store):
    store.add_reference(mentor_key='prutow', source_title='a', topic='main_point',
                        position_text='p', citation='c')
    store.add_reference(mentor_key='prutow', source_title='a', topic='fcf',
                        position_text='q', citation='c')
    mp = store.get_references(topic='main_point')
    assert len(mp) == 1 and mp[0]['topic'] == 'main_point'


def test_get_references_filters_by_mentor_and_topic(store):
    store.add_reference(mentor_key='prutow', source_title='a', topic='main_point',
                        position_text='p', citation='c')
    store.add_reference(mentor_key='chapell', source_title='b', topic='main_point',
                        position_text='q', citation='c')
    out = store.get_references(mentor_key='prutow', topic='main_point')
    assert len(out) == 1 and out[0]['mentor_key'] == 'prutow'


def test_mark_verified_updates_flag(store):
    rid = store.add_reference(
        mentor_key='prutow', source_title='x', topic='y',
        position_text='z', citation='c')
    store.mark_verified(rid)
    refs = store.get_references()
    assert refs[0]['verified'] == 1


def test_delete_reference_removes_row(store):
    rid = store.add_reference(
        mentor_key='prutow', source_title='x', topic='y',
        position_text='z', citation='c')
    store.delete_reference(rid)
    refs = store.get_references()
    assert refs == []


def test_get_references_returns_empty_when_no_match(store):
    assert store.get_references(mentor_key='nobody') == []


def test_list_mentors_returns_distinct_keys(store):
    store.add_reference(mentor_key='prutow', source_title='a', topic='t',
                        position_text='p', citation='c')
    store.add_reference(mentor_key='prutow', source_title='a', topic='u',
                        position_text='q', citation='c')
    store.add_reference(mentor_key='chapell', source_title='b', topic='t',
                        position_text='r', citation='c')
    mentors = store.list_mentors()
    assert set(mentors) == {'prutow', 'chapell'}


def test_seed_examples_inserts_high_confidence_starter(store):
    """The seed call inserts a handful of *carefully-cited* starter entries.

    Per v2 §6.2 + the no-phantom-citation rule, the seed contains only
    positions traceable to a specific named source with a verifiable citation.
    Each entry starts unverified so Bryan confirms against his library.
    """
    n = store.seed_examples()
    assert n >= 1
    refs = store.get_references()
    assert len(refs) == n
    for r in refs:
        assert r['mentor_key']
        assert r['source_title']
        assert r['citation']
        assert r['verified'] == 0  # all seeds need Bryan's verification


def test_seed_examples_is_idempotent(store):
    """Re-seeding does not duplicate entries."""
    store.seed_examples()
    first_count = len(store.get_references())
    store.seed_examples()
    second_count = len(store.get_references())
    assert first_count == second_count
