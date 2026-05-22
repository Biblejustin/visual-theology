"""
E2E test fixtures — boots Flask server as subprocess, provides Playwright helpers.
"""

import os
import signal
import socket
import subprocess
import sys
import tempfile
import time

import pytest
import requests


def _free_port():
    """Find a free TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _port_in_use(port):
    """Check if a port is already in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture(scope="session")
def base_url():
    """Start Flask server as subprocess on a kernel-assigned free port,
    yield base URL, kill on teardown.

    Previously short-circuited to None when port 5111 was busy (the live
    PM2 dev server's port), causing 22 E2E tests to skip. The
    LogosReader native lib is read-only against the Logos files and
    happily co-exists with the desktop app + PM2, so concurrent test
    + live operation is safe. _free_port() asks the kernel for any
    available port — never collides with 5111.

    If the subprocess does fail to start (e.g. unrelated cause), the
    15-second health check below surfaces stderr clearly. Tests then
    error rather than silently skip, which is what we want.
    """
    # Create temp DB
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = tmp.name

    port = _free_port()
    app_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_py = os.path.join(app_dir, "app.py")

    env = os.environ.copy()
    env["COMPANION_DB_PATH"] = db_path
    env["FLASK_PORT"] = str(port)
    env["FLASK_AUTH_BYPASS"] = "1"
    env["PATH"] = "/opt/homebrew/opt/dotnet@8/bin:" + env.get("PATH", "")
    env["DOTNET_ROOT"] = "/opt/homebrew/opt/dotnet@8/libexec"

    # Start server in its own process group for clean teardown
    proc = subprocess.Popen(
        [sys.executable, app_py],
        cwd=app_dir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=os.setsid,
    )

    url = f"http://127.0.0.1:{port}"

    # Health check — wait up to 15s for server to be ready
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            r = requests.get(f"{url}/study/", timeout=2)
            if r.status_code == 200:
                break
        except requests.ConnectionError:
            pass
        time.sleep(0.5)
    else:
        # Server didn't start — dump stderr and fail
        proc.terminate()
        _, stderr = proc.communicate(timeout=5)
        pytest.exit(
            f"Flask server failed to start on port {port}.\n"
            f"stderr: {stderr.decode()[-2000:]}",
            returncode=1,
        )

    yield url

    # Teardown: kill entire process group (Flask + LogosReader grandchild)
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass

    # Clean up temp DB
    try:
        os.unlink(db_path)
    except FileNotFoundError:
        pass


def init_all_app_globals(db_path):
    """Initialize every module-global store the Flask app uses, all pointing
    at the same temp DB, with init_db() called on each.

    Per bug review #7-#8: test fixtures that only set companion_db /
    study_analytics leave session_decisions / pastor_feedback /
    accuser_detector / mentor_store pointing at stale or uninitialized
    DBs. Subsequent requests crashed with 'no such table' on whichever
    store the route happened to touch.

    Use from a pytest fixture:

        @pytest.fixture
        def client():
            import app as app_module
            tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
            tmp.close()
            init_all_app_globals(tmp.name)
            app_module.app.config['TESTING'] = True
            with app_module.app.test_client() as c:
                yield c
            os.unlink(tmp.name)
    """
    import app as app_module
    from companion_db import CompanionDB
    from session_analytics import SessionAnalytics
    from accuser_detector import AccuserDetector
    from mentor_reference_store import MentorReferenceStore
    from pastor_feedback_store import PastorFeedbackStore
    from session_decisions import SessionDecisionStore
    from seed_questions import seed_question_bank

    cdb = CompanionDB(db_path)
    cdb.init_db()
    seed_question_bank(cdb)

    analytics = SessionAnalytics(db_path)
    analytics.init_db()

    accuser = AccuserDetector(db_path)
    accuser.init_db()

    mentor = MentorReferenceStore(db_path)
    mentor.init_db()

    feedback = PastorFeedbackStore(db_path)
    feedback.init_db()

    decisions = SessionDecisionStore(db_path)
    decisions.init_db()

    app_module.companion_db = cdb
    app_module.study_analytics = analytics
    app_module.accuser_detector = accuser
    app_module.mentor_store = mentor
    app_module.pastor_feedback = feedback
    app_module.session_decisions = decisions

    # Reset the db_provider cache so get_db() reads the new db_path env var.
    # (get_db() lives in db_provider.py after the polish #5b blueprint split.)
    import db_provider
    db_provider._db_instance = None
    import os as _os
    _os.environ['COMPANION_DB_PATH'] = db_path
    return {
        'companion_db': cdb,
        'study_analytics': analytics,
        'accuser_detector': accuser,
        'mentor_store': mentor,
        'pastor_feedback': feedback,
        'session_decisions': decisions,
    }


def _require_base_url(base_url):
    """Helper: skip the calling test cleanly when the live dev server is
    holding port 5111 (in which case base_url yielded None). Without this
    skip, page.goto(f'{None}/...') produced cryptic 'Cannot navigate to
    invalid URL: None/study/' Playwright errors. Per bug review #7."""
    if base_url is None:
        import pytest
        pytest.skip(
            "Live dev server is using port 5111 — E2E subprocess server "
            "couldn't start. Stop the dev server to run E2E tests."
        )


def create_session(page, base_url, passage="Romans 1:24-32"):
    """Create a study session and return the session URL."""
    _require_base_url(base_url)
    page.goto(f"{base_url}/study/")
    page.fill('input[name="passage"]', passage)
    page.click('button[type="submit"], .btn-primary')
    page.wait_for_url("**/study/session/*")
    return page.url


def advance_to_conversation(page, base_url, passage="Romans 1:24-32"):
    """Create session and click through all 4 card phases to reach conversation mode.

    Phase behavior:
    - prayer (has textarea): fills text, clicks Next
    - read_translate (has textarea): fills text, clicks Next
    - digestion (has textarea): fills text, clicks Next
    - study_bibles (no textarea): just clicks Next
    """
    _require_base_url(base_url)
    create_session(page, base_url, passage)

    # Phase 1: Prayer (has textarea)
    page.fill("#card-response", "Lord, open my eyes to this passage.")
    page.click("#card-next-form button[type='submit']")
    page.wait_for_load_state("networkidle")

    # Phase 2: Read & Translate (has textarea)
    page.fill("#card-response", "Working translation of the passage.")
    page.click("#card-next-form button[type='submit']")
    page.wait_for_load_state("networkidle")

    # Phase 3: Digestion (has textarea)
    page.fill("#card-response", "Meditation notes on the text.")
    page.click("#card-next-form button[type='submit']")
    page.wait_for_load_state("networkidle")

    # Phase 4: Study Bibles (no textarea, just Next)
    page.click("#card-next-form button[type='submit']")
    page.wait_for_load_state("networkidle")

    # Should now be in conversation mode
    assert page.locator("#conversation-column").count() > 0, \
        "Expected conversation mode after advancing through all card phases"

    return page.url


def send_message_and_wait(page, message, timeout=90000):
    """Type a message, send it, wait for assistant response to finish streaming.

    Returns the last .conv-message.assistant element.
    """
    page.fill("#study-input", message)
    page.click("#btn-study-send")

    # Wait for send button to re-enable (streaming finished)
    page.wait_for_function(
        "() => !document.getElementById('btn-study-send').disabled",
        timeout=timeout,
    )

    # Get last assistant message
    msgs = page.locator(".conv-message.assistant")
    assert msgs.count() > 0, "No assistant message appeared"
    last_msg = msgs.last

    # Check for error indicators
    content = last_msg.inner_text()
    assert "[Error" not in content, f"Response contained error: {content[:200]}"
    assert "[Connection error" not in content, f"Response contained connection error: {content[:200]}"

    return last_msg


def get_tool_results(page):
    """Get all tool results from conversation thread.

    Returns list of dicts: {"name": "Bible", "body": "..."}.
    Also checks .insight-pill elements (used by save_to_outline).
    """
    results = []

    # Tool results render in the Sources panel as `.source-card` (replacing
    # the in-thread `.conv-tool-result` design). Only finished cards have
    # a <summary>; pending placeholders don't, so they're skipped.
    tool_els = page.locator(".source-card:not(.pending)")
    for i in range(tool_els.count()):
        el = tool_els.nth(i)
        summary = el.locator("summary")
        body = el.locator(".tool-body")
        results.append({
            "name": summary.inner_text() if summary.count() > 0 else "",
            "body": body.inner_text() if body.count() > 0 else "",
        })

    # Insight pills (save_to_outline renders these instead)
    pills = page.locator(".insight-pill")
    for i in range(pills.count()):
        results.append({
            "name": "saving to outline",
            "body": pills.nth(i).inner_text(),
        })

    return results
