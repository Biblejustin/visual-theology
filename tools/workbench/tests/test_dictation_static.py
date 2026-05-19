import os

import pytest


# Override pytest-base-url's autouse _verify_url fixture so these static tests
# don't trigger conftest.py's Flask-server `base_url` fixture.
@pytest.fixture(scope="session", autouse=True)
def _verify_url():
    yield


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _read(relative_path):
    with open(os.path.join(ROOT, relative_path), encoding="utf-8") as f:
        return f.read()


def test_dictation_retries_default_mic_when_selected_device_constraint_fails():
    js = _read("static/dictation.js")

    assert "deviceId: { exact:" not in js
    assert "shouldRetryWithDefaultMic" in js
    assert "localStorage.removeItem(STORAGE_KEY)" in js
    assert "getAudioConstraints('')" in js
    assert "startLevelMeter" in js
    assert "Recording - stop for transcript" in js
    assert "Starting..." in js
    assert "No audio captured" in js
    assert "No words recognized" in js


def test_study_session_uses_current_dictation_asset_version():
    """Just verify cache-busters are present on the assets that drive
    the dictation UI. Pinning to a literal version made the test brittle
    every time we shipped a CSS change."""
    import re
    html = _read("templates/study_session.html")

    assert re.search(r'/static/study\.css\?v=\d+', html), \
        'study.css cache-busted version not found'
    assert re.search(r'/static/dictation\.js\?v=\d+', html), \
        'dictation.js cache-busted version not found'


def test_mic_picker_hidden_state_is_not_overridden_by_flex_display():
    css = _read("static/study.css")
    partial = _read("templates/partials/mic_button.html")

    assert ".mic-picker[hidden]" in css
    assert "display: none" in css
    assert "mic-level" in partial
    assert "mic-readout" in partial
    assert 'aria-expanded="false"' in partial
