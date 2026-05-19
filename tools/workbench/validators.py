"""Shared request validators (per project assessment polish #2).

Reusable validation helpers that route handlers should call instead of
re-implementing the same try/except / type-check / range-clamp logic per
route. They raise ValidationError (or return (value, error_response)
tuples) and the route handlers translate that into HTTP responses.

Two ergonomic patterns are offered:

1. Direct (functional, recommended for new routes):

       from validators import parse_int_arg, json_body, require_string

       @app.route(...)
       def my_route():
           data, err = json_body()
           if err: return err
           name, err = require_string(data, "name", min_length=1)
           if err: return err
           limit, err = parse_int_arg("limit", default=20, lo=1, hi=200)
           if err: return err
           ...

2. Exception (when you'd rather try/except, e.g. inside larger flows):

       try:
           name = require_string_or_raise(data, "name")
       except ValidationError as e:
           return jsonify({"error": str(e)}), 400

Both patterns return Flask responses directly so routes stay flat.

NOTE: existence checks (require_session, require_sermon) need the DB
handles to be available. They live here as small helpers that take the
handle as their first argument — no module-level globals.
"""

from typing import Any, Optional, Tuple

from flask import jsonify, request


class ValidationError(ValueError):
    """Raised by *_or_raise helpers when a request payload fails validation."""


# ── Type checks ────────────────────────────────────────────────────────

def require_string(data: dict, key: str, *,
                   min_length: int = 0,
                   max_length: Optional[int] = None,
                   strip: bool = True,
                   allow_missing: bool = False,
                   default: Optional[str] = None) -> Tuple[Optional[str], Optional[Any]]:
    """Return (value, error_response). One is always None.

    - Missing + not allow_missing → 400
    - Wrong type → 400
    - Empty after strip → 400 if min_length > 0
    - Too long → 400
    """
    if key not in data:
        if allow_missing:
            return default, None
        return None, (jsonify({"error": f"{key} required"}), 400)
    value = data[key]
    if not isinstance(value, str):
        return None, (jsonify({"error": f"{key} must be a string"}), 400)
    if strip:
        value = value.strip()
    if len(value) < min_length:
        return None, (jsonify({"error": f"{key} must be at least {min_length} chars"}), 400)
    if max_length is not None and len(value) > max_length:
        return None, (jsonify({"error": f"{key} exceeds {max_length} chars"}), 400)
    return value, None


def require_list_of_strings(data: dict, key: str, *,
                            allow_missing: bool = False,
                            default: Optional[list] = None) -> Tuple[Optional[list], Optional[Any]]:
    """Return (value, error_response). One is always None.

    - Missing + not allow_missing → 400
    - Wrong type (not list) → 400
    - List with non-string element → 400
    """
    if key not in data:
        if allow_missing:
            return default, None
        return None, (jsonify({"error": f"{key} required"}), 400)
    value = data[key]
    if not isinstance(value, list):
        return None, (jsonify({"error": f"{key} must be a list of strings"}), 400)
    if not all(isinstance(s, str) for s in value):
        return None, (jsonify({"error": f"{key} must be a list of strings"}), 400)
    return value, None


def require_int(data: dict, key: str, *,
                lo: Optional[int] = None,
                hi: Optional[int] = None,
                allow_missing: bool = False,
                default: Optional[int] = None) -> Tuple[Optional[int], Optional[Any]]:
    """Return (value, error_response). Validates type + range."""
    if key not in data:
        if allow_missing:
            return default, None
        return None, (jsonify({"error": f"{key} required"}), 400)
    raw = data[key]
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, (jsonify({"error": f"{key} must be an integer"}), 400)
    if lo is not None and value < lo:
        return None, (jsonify({"error": f"{key} must be >= {lo}"}), 400)
    if hi is not None and value > hi:
        return None, (jsonify({"error": f"{key} must be <= {hi}"}), 400)
    return value, None


# ── Query-string helpers ──────────────────────────────────────────────

def parse_int_arg(name: str, *,
                  default: int,
                  lo: Optional[int] = None,
                  hi: Optional[int] = None) -> int:
    """Parse a query-string int safely. Returns default on invalid input,
    then clamps to [lo, hi] when provided. Never raises — query strings
    come from outside, validation lives at the boundary.

    This is the function form the assessment recommended (parse_int_arg).
    """
    raw = request.args.get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    if lo is not None:
        value = max(lo, value)
    if hi is not None:
        value = min(hi, value)
    return value


def json_body() -> Tuple[dict, Optional[Any]]:
    """Return (data, error_response). Always a dict on success — coerces
    None and non-dict bodies. silent=True so Flask doesn't 415 before
    our validation runs."""
    data = request.get_json(silent=True)
    if data is None:
        return {}, None
    if not isinstance(data, dict):
        return {}, (jsonify({"error": "request body must be a JSON object"}), 400)
    return data, None


# ── Row existence checks ──────────────────────────────────────────────

def require_session(companion_db, session_id: int) -> Tuple[Optional[dict], Optional[Any]]:
    """Return (session_dict, error_response). 404 if missing.

    First-arg companion_db so this helper has no module-global coupling.
    """
    if companion_db is None:
        return None, (jsonify({"error": "service unavailable"}), 503)
    session = companion_db.get_session(session_id)
    if not session:
        return None, (jsonify({"error": "session not found"}), 404)
    return session, None


def require_sermon_row(companion_db, sermon_id: int) -> Tuple[Optional[dict], Optional[Any]]:
    """Return ({'id': sermon_id}, error_response). 404 if missing."""
    if companion_db is None:
        return None, (jsonify({"error": "service unavailable"}), 503)
    conn = companion_db._conn()
    try:
        row = conn.execute(
            "SELECT id FROM sermons WHERE id = ?", (sermon_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None, (jsonify({"error": "sermon not found"}), 404)
    return {"id": sermon_id}, None


# ── Raising variants (when try/except reads cleaner) ──────────────────

def require_string_or_raise(data: dict, key: str, *,
                            min_length: int = 0,
                            max_length: Optional[int] = None,
                            strip: bool = True) -> str:
    """Inline-validates without going through jsonify (no Flask request
    context required). Use when try/except reads cleaner than tuple
    unpacking."""
    if key not in data:
        raise ValidationError(f"{key} required")
    value = data[key]
    if not isinstance(value, str):
        raise ValidationError(f"{key} must be a string")
    if strip:
        value = value.strip()
    if len(value) < min_length:
        raise ValidationError(f"{key} must be at least {min_length} chars")
    if max_length is not None and len(value) > max_length:
        raise ValidationError(f"{key} exceeds {max_length} chars")
    return value


def require_int_or_raise(data: dict, key: str, *,
                         lo: Optional[int] = None,
                         hi: Optional[int] = None) -> int:
    """Inline-validates without Flask request context."""
    if key not in data:
        raise ValidationError(f"{key} required")
    try:
        value = int(data[key])
    except (TypeError, ValueError):
        raise ValidationError(f"{key} must be an integer")
    if lo is not None and value < lo:
        raise ValidationError(f"{key} must be >= {lo}")
    if hi is not None and value > hi:
        raise ValidationError(f"{key} must be <= {hi}")
    return value
