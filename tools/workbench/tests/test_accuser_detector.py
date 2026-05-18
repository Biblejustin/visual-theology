"""Tests for accuser-voice detection (Cycle 2a: detection-only, no rebuttal).

Per v2 spec §9.1, four trigger categories:
- self_condemnation
- asymmetric_attribution
- punitive_standard
- self_disqualifying

This is calibration-phase: high precision required (false positives erode trust),
recall can grow as patterns refine from real-data logs.
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from accuser_detector import AccuserDetector


@pytest.fixture
def det():
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    d = AccuserDetector(tmp.name)
    d.init_db()
    yield d
    os.unlink(tmp.name)


# ── Self-condemnation patterns ─────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "I cheated on this sermon.",
    "I phoned in the application this week.",
    "I was lazy in the exegesis.",
    "I'm lazy and the congregation deserves better.",
    "This whole thing wasn't mine.",
    "I didn't earn this insight.",
    "Honestly I just phoned it in.",
])
def test_self_condemnation_matches(det, text):
    matches = det.detect(text)
    assert any(m['category'] == 'self_condemnation' for m in matches), \
        f"Expected self_condemnation match in: {text!r}\nGot: {matches}"


# ── Asymmetric attribution patterns ────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "The LLM wrote the slave-market scene.",
    "Claude gave me the burden statement.",
    "The AI did all the work on the Christ thread.",
    "The model wrote my conclusion.",
    "The assistant made the application for me.",
])
def test_asymmetric_attribution_matches(det, text):
    matches = det.detect(text)
    assert any(m['category'] == 'asymmetric_attribution' for m in matches), \
        f"Expected asymmetric_attribution match in: {text!r}\nGot: {matches}"


# ── Punitive standard patterns ─────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Prutow requires six memorized points.",
    "Chapell demands a clear FCF in every sermon.",
    "York expects 25-minute sermons minimum.",
    "Robinson says the Big Idea must be a complete sentence.",
    "Micah preaches without notes and so should I.",
    "Pastor Smith requires hand-written manuscripts.",
])
def test_punitive_standard_matches(det, text):
    matches = det.detect(text)
    assert any(m['category'] == 'punitive_standard' for m in matches), \
        f"Expected punitive_standard match in: {text!r}\nGot: {matches}"


# ── Self-disqualifying patterns ────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Yeah it landed, but it was on the fly.",
    "But it was accidental — I didn't plan that.",
    "I just got lucky with the timing.",
    "I had felt but not seen what the text was doing.",
])
def test_self_disqualifying_matches(det, text):
    matches = det.detect(text)
    assert any(m['category'] == 'self_disqualifying' for m in matches), \
        f"Expected self_disqualifying match in: {text!r}\nGot: {matches}"


# ── No false positives on benign text ──────────────────────────────────────

@pytest.mark.parametrize("text", [
    "What does Romans 1:18 mean by 'wrath of God'?",
    "The text uses apokalupto in the present tense.",
    "I want to wrestle with the Christ-thread here.",
    "How does Bavinck handle this locus?",
    "Help me find a cross-reference for verse 21.",
    "Paul says he was the chief of sinners — interesting framing.",  # quoting another
    "The exegesis is dense but coherent.",
])
def test_no_false_positive_on_benign_text(det, text):
    matches = det.detect(text)
    assert matches == [], f"Unexpected match in benign text: {text!r}\nGot: {matches}"


# ── Schema ─────────────────────────────────────────────────────────────────

def test_init_db_creates_table(det):
    conn = det._conn()
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='accuser_detections'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1


# ── Logging ────────────────────────────────────────────────────────────────

def test_analyze_message_logs_single_match(det):
    text = "I cheated on this sermon."
    n = det.analyze_message(
        session_id=42, message_id=100, surface='study', text=text)
    assert n == 1
    conn = det._conn()
    rows = conn.execute("SELECT * FROM accuser_detections").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0]['session_id'] == 42
    assert rows[0]['message_id'] == 100
    assert rows[0]['surface'] == 'study'
    assert rows[0]['trigger_category'] == 'self_condemnation'
    assert rows[0]['full_text'] == text


def test_analyze_message_logs_multiple_matches(det):
    text = "The LLM wrote it and I just got lucky — wasn't mine at all."
    n = det.analyze_message(
        session_id=1, message_id=1, surface='companion', text=text)
    assert n >= 2  # at minimum: asymmetric_attribution + self_condemnation/disqualifying
    conn = det._conn()
    rows = conn.execute("SELECT trigger_category FROM accuser_detections").fetchall()
    conn.close()
    categories = {r['trigger_category'] for r in rows}
    assert 'asymmetric_attribution' in categories
    # at least one of the self-* categories
    assert categories & {'self_condemnation', 'self_disqualifying'}


def test_analyze_message_skips_very_short_text(det):
    n = det.analyze_message(
        session_id=1, message_id=1, surface='study', text='ok')
    assert n == 0


def test_analyze_message_skips_empty_text(det):
    n = det.analyze_message(
        session_id=1, message_id=1, surface='study', text='')
    assert n == 0


def test_analyze_message_handles_none_text(det):
    n = det.analyze_message(
        session_id=1, message_id=1, surface='study', text=None)
    assert n == 0


def test_analyze_message_with_no_matches_logs_nothing(det):
    n = det.analyze_message(
        session_id=1, message_id=1, surface='study',
        text="What does the Greek dikaiosune mean here?")
    assert n == 0
    conn = det._conn()
    rows = conn.execute("SELECT * FROM accuser_detections").fetchall()
    conn.close()
    assert len(rows) == 0


# ── Querying detections ────────────────────────────────────────────────────

def test_get_detections_filters_by_session(det):
    det.analyze_message(session_id=1, message_id=1, surface='study',
                        text="I cheated on this sermon.")
    det.analyze_message(session_id=2, message_id=2, surface='study',
                        text="I phoned it in.")
    s1 = det.get_detections(session_id=1)
    s2 = det.get_detections(session_id=2)
    assert len(s1) == 1
    assert len(s2) == 1
    assert s1[0]['session_id'] == 1
    assert s2[0]['session_id'] == 2


def test_get_detections_filters_by_category(det):
    det.analyze_message(session_id=1, message_id=1, surface='study',
                        text="I cheated on this sermon.")
    det.analyze_message(session_id=1, message_id=2, surface='study',
                        text="The LLM wrote the conclusion.")
    sc = det.get_detections(category='self_condemnation')
    aa = det.get_detections(category='asymmetric_attribution')
    assert len(sc) == 1
    assert sc[0]['trigger_category'] == 'self_condemnation'
    assert len(aa) == 1
    assert aa[0]['trigger_category'] == 'asymmetric_attribution'


def test_get_detections_no_filter_returns_all(det):
    det.analyze_message(session_id=1, message_id=1, surface='study',
                        text="I cheated on this sermon.")
    det.analyze_message(session_id=2, message_id=2, surface='companion',
                        text="The LLM wrote my conclusion.")
    all_d = det.get_detections()
    assert len(all_d) == 2
