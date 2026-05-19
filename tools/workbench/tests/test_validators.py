"""Tests for the shared validators module (polish #2)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from validators import (
    ValidationError,
    parse_int_arg,
    json_body,
    require_string,
    require_list_of_strings,
    require_int,
    require_session,
    require_sermon_row,
    require_string_or_raise,
    require_int_or_raise,
)


@pytest.fixture
def app_ctx():
    """Tiny Flask app for request-context helpers."""
    from flask import Flask
    app = Flask(__name__)
    return app


# ── require_string ────────────────────────────────────────────────────

def test_require_string_returns_value_on_success(app_ctx):
    with app_ctx.test_request_context():
        v, err = require_string({"name": "  Bryan "}, "name")
        assert v == "Bryan"
        assert err is None


def test_require_string_400_when_missing(app_ctx):
    with app_ctx.test_request_context():
        v, err = require_string({}, "name")
        assert v is None
        assert err[1] == 400


def test_require_string_allows_missing_when_flagged(app_ctx):
    with app_ctx.test_request_context():
        v, err = require_string({}, "name", allow_missing=True, default="anon")
        assert v == "anon"
        assert err is None


def test_require_string_400_when_not_string(app_ctx):
    with app_ctx.test_request_context():
        _, err = require_string({"name": 42}, "name")
        assert err[1] == 400


def test_require_string_400_when_below_min_length(app_ctx):
    with app_ctx.test_request_context():
        _, err = require_string({"name": ""}, "name", min_length=1)
        assert err[1] == 400


def test_require_string_400_when_above_max_length(app_ctx):
    with app_ctx.test_request_context():
        _, err = require_string({"name": "x" * 100}, "name", max_length=50)
        assert err[1] == 400


# ── require_list_of_strings ──────────────────────────────────────────

def test_require_list_of_strings_returns_list(app_ctx):
    with app_ctx.test_request_context():
        v, err = require_list_of_strings({"items": ["a", "b"]}, "items")
        assert v == ["a", "b"]
        assert err is None


def test_require_list_of_strings_400_when_int_in_list(app_ctx):
    with app_ctx.test_request_context():
        _, err = require_list_of_strings({"items": ["a", 2]}, "items")
        assert err[1] == 400


def test_require_list_of_strings_400_when_not_list(app_ctx):
    with app_ctx.test_request_context():
        _, err = require_list_of_strings({"items": "abc"}, "items")
        assert err[1] == 400


def test_require_list_of_strings_400_when_missing(app_ctx):
    with app_ctx.test_request_context():
        _, err = require_list_of_strings({}, "items")
        assert err[1] == 400


# ── require_int ──────────────────────────────────────────────────────

def test_require_int_returns_value(app_ctx):
    with app_ctx.test_request_context():
        v, err = require_int({"n": "42"}, "n")
        assert v == 42
        assert err is None


def test_require_int_400_on_non_numeric(app_ctx):
    with app_ctx.test_request_context():
        _, err = require_int({"n": "abc"}, "n")
        assert err[1] == 400


def test_require_int_400_below_lo(app_ctx):
    with app_ctx.test_request_context():
        _, err = require_int({"n": -5}, "n", lo=0)
        assert err[1] == 400


def test_require_int_400_above_hi(app_ctx):
    with app_ctx.test_request_context():
        _, err = require_int({"n": 1000}, "n", hi=100)
        assert err[1] == 400


# ── parse_int_arg ────────────────────────────────────────────────────

def test_parse_int_arg_uses_default_when_missing(app_ctx):
    with app_ctx.test_request_context("/?nothing=here"):
        assert parse_int_arg("limit", default=30) == 30


def test_parse_int_arg_uses_default_when_invalid(app_ctx):
    with app_ctx.test_request_context("/?limit=x"):
        assert parse_int_arg("limit", default=30) == 30


def test_parse_int_arg_clamps_lo(app_ctx):
    with app_ctx.test_request_context("/?limit=-50"):
        assert parse_int_arg("limit", default=30, lo=1) == 1


def test_parse_int_arg_clamps_hi(app_ctx):
    with app_ctx.test_request_context("/?limit=9999"):
        assert parse_int_arg("limit", default=30, hi=200) == 200


def test_parse_int_arg_passes_through_valid(app_ctx):
    with app_ctx.test_request_context("/?limit=42"):
        assert parse_int_arg("limit", default=30, lo=1, hi=200) == 42


# ── json_body ────────────────────────────────────────────────────────

def test_json_body_returns_dict_on_success(app_ctx):
    with app_ctx.test_request_context(
            "/", method="POST", json={"name": "Bryan"}):
        data, err = json_body()
        assert data == {"name": "Bryan"}
        assert err is None


def test_json_body_returns_empty_dict_when_no_body(app_ctx):
    with app_ctx.test_request_context("/", method="POST"):
        data, err = json_body()
        assert data == {}
        assert err is None


def test_json_body_400_when_non_object(app_ctx):
    with app_ctx.test_request_context("/", method="POST", json=[1, 2, 3]):
        data, err = json_body()
        assert err is not None
        assert err[1] == 400


# ── require_session ──────────────────────────────────────────────────

class _FakeDB:
    def __init__(self, sessions=None):
        self._sessions = sessions or {}

    def get_session(self, sid):
        return self._sessions.get(sid)


def test_require_session_returns_dict_on_hit(app_ctx):
    with app_ctx.test_request_context():
        db = _FakeDB(sessions={1: {"id": 1, "passage_ref": "Rom 1"}})
        s, err = require_session(db, 1)
        assert s["id"] == 1
        assert err is None


def test_require_session_404_on_miss(app_ctx):
    with app_ctx.test_request_context():
        s, err = require_session(_FakeDB(), 999)
        assert s is None
        assert err[1] == 404


def test_require_session_503_when_db_is_none(app_ctx):
    with app_ctx.test_request_context():
        s, err = require_session(None, 1)
        assert err[1] == 503


# ── *_or_raise variants ──────────────────────────────────────────────

def test_require_string_or_raise_returns_value():
    assert require_string_or_raise({"name": "Bryan"}, "name") == "Bryan"


def test_require_string_or_raise_raises_validation_error():
    with pytest.raises(ValidationError):
        require_string_or_raise({}, "name")


def test_require_int_or_raise_returns_value():
    assert require_int_or_raise({"n": "42"}, "n") == 42


def test_require_int_or_raise_raises_validation_error():
    with pytest.raises(ValidationError):
        require_int_or_raise({"n": "abc"}, "n")
