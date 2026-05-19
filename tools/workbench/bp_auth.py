"""Auth Flask blueprint.

Second blueprint extraction (polish #5b, 2 of 6). Holds /login, /logout,
and the app-wide before_request auth gate.

Uses `before_app_request` (not `before_request`) so the gate runs on
every request including routes registered by other blueprints — the
same semantics as the original `@app.before_request` decorator.

Credentials come from the shared MightyOaks LMS admins table.
"""

import os
import sqlite3

import bcrypt
from flask import Blueprint, jsonify, redirect, request, session


LMS_DB_PATH = "/Volumes/NetworkStorage/hybrid/data/lms.db"


def verify_admin(username, password):
    """Check credentials against MightyOaks LMS admins table."""
    try:
        conn = sqlite3.connect(LMS_DB_PATH)
        row = conn.execute(
            "SELECT password_hash FROM admins WHERE username = ?", (username,)
        ).fetchone()
        conn.close()
        if row and bcrypt.checkpw(password.encode(), row[0].encode()):
            return True
    except Exception:
        pass
    return False


LOGIN_PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sermon Tool — Login</title>
<style>
  body{font-family:system-ui,sans-serif;display:flex;justify-content:center;align-items:center;
       min-height:100vh;margin:0;background:#1a1a2e;color:#e0e0e0}
  form{background:#16213e;padding:2rem;border-radius:12px;width:320px;box-shadow:0 4px 24px rgba(0,0,0,.4)}
  h2{margin:0 0 1.5rem;text-align:center;color:#a8d8ea}
  input{width:100%;padding:.75rem;margin:.5rem 0;border:1px solid #333;border-radius:6px;
        background:#0f3460;color:#e0e0e0;box-sizing:border-box;font-size:1rem}
  button{width:100%;padding:.75rem;margin-top:1rem;border:none;border-radius:6px;
         background:#e94560;color:#fff;font-size:1rem;cursor:pointer;font-weight:600}
  button:hover{background:#c73e54}
  .err{color:#e94560;text-align:center;margin-top:.5rem;font-size:.9rem}
</style></head><body>
<form method="POST" action="/login">
  <h2>Sermon Tool</h2>
  <input name="username" placeholder="Username" required autofocus>
  <input name="password" type="password" placeholder="Password" required>
  <button type="submit">Log In</button>
  {error}
</form></body></html>"""


bp_auth = Blueprint("auth", __name__)


@bp_auth.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return LOGIN_PAGE.replace("{error}", "")
    username = request.form.get("username", "")
    password = request.form.get("password", "")
    if verify_admin(username, password):
        session["authenticated"] = True
        return redirect("/")
    return LOGIN_PAGE.replace("{error}", '<p class="err">Invalid credentials</p>')


@bp_auth.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@bp_auth.before_app_request
def require_auth():
    """App-wide auth gate. before_app_request so it covers every blueprint."""
    from flask import current_app
    if request.path == "/login" or request.path.startswith("/static/"):
        return
    if current_app.config.get("TESTING") or os.environ.get("FLASK_AUTH_BYPASS") == "1":
        if request.path == "/":
            return redirect("/study/")
        return
    if not session.get("authenticated"):
        # Per bug review #5: API / non-GET requests (e.g. PATCH /clock,
        # POST /decisions/set, POST /transcribe) must NOT 302 to /login.
        # Browsers / fetch() follow the redirect with the original method,
        # producing 'PATCH /login → 405' floods in PM2 logs and silent
        # client-side failures. Return 401 JSON for those callers so they
        # can detect the expired session and prompt re-login explicitly.
        wants_json = (
            request.method != "GET" or
            request.path.startswith("/api/") or
            (request.accept_mimetypes.best == "application/json")
        )
        if wants_json:
            return jsonify({
                "error": "authentication required",
                "login_url": "/login",
            }), 401
        return redirect("/login")
    # Redirect root to the study tool
    if request.path == "/":
        return redirect("/study/")
