import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from homiletics_engine import Insight, ANALYZER_KINDS


@pytest.fixture(scope="session", autouse=True)
def _verify_url():
    yield


def test_insight_is_frozen_dataclass():
    i = Insight(kind='illustration', title='t', body='b',
                citations=['LLS:X'], confidence=0.5, metadata={'k': 'v'})
    with pytest.raises(Exception):
        i.title = 'changed'  # frozen


def test_insight_defaults():
    i = Insight(kind='illustration', title='t', body='b',
                citations=[], confidence=0.5)
    assert i.metadata == {}


def test_analyzer_kinds_match_schema():
    # Must match the CHECK constraint in homiletics_insights schema.
    assert ANALYZER_KINDS == {
        'illustration', 'objection', 'counseling', 'wisdom',
        'christ_thread', 'balance_flag', 'coherence_flag',
    }


def test_insight_kind_validation():
    with pytest.raises(ValueError):
        Insight(kind='not_a_kind', title='t', body='b',
                citations=[], confidence=0.5)


def test_insight_confidence_range():
    with pytest.raises(ValueError):
        Insight(kind='illustration', title='t', body='b',
                citations=[], confidence=1.5)
    with pytest.raises(ValueError):
        Insight(kind='illustration', title='t', body='b',
                citations=[], confidence=-0.1)
