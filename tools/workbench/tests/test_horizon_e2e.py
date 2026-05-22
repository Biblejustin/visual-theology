"""E2E: the week-horizon countdown refreshes live in a real browser.

Fix 2026-05-22: the "to Friday 3pm" label was server-rendered once and never
ticked (study.js had no ticker), so it froze until a page reload. study.js now
calls refreshHorizon() on load and every 60s, polling /horizon. This verifies
the poller actually fires in the browser and the page has no console errors.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from conftest import create_session


def test_horizon_polled_on_load_without_console_errors(page, base_url):
    horizon_calls = []
    console_errors = []
    page.on("request", lambda r: horizon_calls.append(r.url) if "/horizon" in r.url else None)
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)

    create_session(page, base_url, "Romans 3:25-26")

    # refreshHorizon() runs on DOMContentLoaded; give it a moment to fire.
    page.wait_for_timeout(2000)

    assert any("/horizon" in u for u in horizon_calls), \
        "study.js did not poll /horizon on load — countdown would stay frozen"
    # The label should be present and non-empty after the refresh.
    page.wait_for_selector(".horizon-tooltip")
    assert page.locator(".horizon-tooltip").inner_text().strip(), "horizon label empty"
    assert not console_errors, f"console errors on study page: {console_errors}"
