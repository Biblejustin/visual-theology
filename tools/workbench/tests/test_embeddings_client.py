import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from embeddings_client import CannedEmbeddingsClient


@pytest.fixture(scope="session", autouse=True)
def _verify_url():
    yield


def test_canned_client_returns_deterministic_vectors_by_text():
    c = CannedEmbeddingsClient(dimension=8)
    v1 = c.embed_one("hello world")
    v2 = c.embed_one("hello world")
    assert v1 == v2
    assert len(v1) == 8
    assert all(isinstance(x, float) for x in v1)


def test_canned_client_different_text_different_vectors():
    c = CannedEmbeddingsClient(dimension=8)
    v1 = c.embed_one("Christ propitiates")
    v2 = c.embed_one("the wages of sin")
    assert v1 != v2


def test_canned_client_embed_batch():
    c = CannedEmbeddingsClient(dimension=8)
    vs = c.embed(["a", "b", "c"])
    assert len(vs) == 3
    assert all(len(v) == 8 for v in vs)
    # Single calls match batch calls
    assert c.embed_one("a") == vs[0]


def test_canned_client_vectors_are_unit_normalized():
    import math
    c = CannedEmbeddingsClient(dimension=8)
    v = c.embed_one("Christ")
    norm = math.sqrt(sum(x * x for x in v))
    assert abs(norm - 1.0) < 1e-6


def test_canned_client_dimension_property():
    c = CannedEmbeddingsClient(dimension=16)
    assert c.dimension == 16
