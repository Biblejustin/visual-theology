"""Unit tests for whisper_service — faster-whisper wrapper.

The real model is too heavy to load in tests, so we inject a fake model via
``set_model_for_testing``. The tests verify the wrapper's wiring: how it feeds
audio bytes into the model, how it joins segments, and how it handles errors.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest


# Override pytest-base-url's autouse _verify_url fixture so these pure unit
# tests don't trigger conftest.py's Flask-server `base_url` fixture.
@pytest.fixture(scope="session", autouse=True)
def _verify_url():
    yield


class _FakeSegment:
    def __init__(self, text, start=0.0, end=0.0, no_speech_prob=0.05, avg_logprob=-0.3):
        self.text = text
        self.start = start
        self.end = end
        self.no_speech_prob = no_speech_prob
        self.avg_logprob = avg_logprob


class _FakeInfo:
    def __init__(self, duration=1.5, language='en'):
        self.duration = duration
        self.language = language


class _FakeModel:
    """Stand-in for faster_whisper.WhisperModel."""

    def __init__(self, segments_text=None, duration=1.5, language='en',
                 raise_on_transcribe=None, segments=None):
        # Allow tests to pass either text strings (legacy) or full segment objects.
        if segments is not None:
            self._segments = segments
        else:
            self._segments = [_FakeSegment(t) for t in (segments_text or ['hello world'])]
        self.duration = duration
        self.language = language
        self.raise_on_transcribe = raise_on_transcribe
        self.last_audio_path = None
        self.last_kwargs = None

    def transcribe(self, audio_path, **kwargs):
        self.last_audio_path = audio_path
        self.last_kwargs = kwargs
        if self.raise_on_transcribe:
            raise self.raise_on_transcribe
        return iter(self._segments), _FakeInfo(self.duration, self.language)


@pytest.fixture(autouse=True)
def _reset_module_state():
    import whisper_service
    whisper_service._reset_for_testing()
    yield
    whisper_service._reset_for_testing()


def test_transcribe_joins_segments_into_text():
    import whisper_service
    fake = _FakeModel(segments_text=[' Hello', ' world.', ' How are you?'])
    whisper_service.set_model_for_testing(fake)

    result = whisper_service.transcribe_audio(b'fake-webm-bytes', content_type='audio/webm')

    assert result['text'] == 'Hello world. How are you?'


def test_transcribe_returns_duration_and_language():
    import whisper_service
    fake = _FakeModel(segments_text=['hi'], duration=2.25, language='en')
    whisper_service.set_model_for_testing(fake)

    result = whisper_service.transcribe_audio(b'\x00\x01', content_type='audio/webm')

    assert result['duration_sec'] == pytest.approx(2.25)
    assert result['language'] == 'en'


def test_transcribe_writes_audio_to_tempfile_and_cleans_up():
    import whisper_service
    fake = _FakeModel(segments_text=['ok'])
    whisper_service.set_model_for_testing(fake)

    whisper_service.transcribe_audio(b'audio-bytes', content_type='audio/webm')

    assert fake.last_audio_path is not None
    # File should be gone after the call returns
    assert not os.path.exists(fake.last_audio_path), \
        'Temp audio file should be cleaned up after transcription'


def test_transcribe_empty_audio_raises():
    import whisper_service
    with pytest.raises(ValueError, match='empty'):
        whisper_service.transcribe_audio(b'', content_type='audio/webm')


def test_transcribe_uses_suffix_matching_content_type():
    """webm -> .webm, mp4 -> .mp4, wav -> .wav (helps ffmpeg sniff format)."""
    import whisper_service
    fake = _FakeModel(segments_text=['ok'])
    whisper_service.set_model_for_testing(fake)

    whisper_service.transcribe_audio(b'x', content_type='audio/mp4')
    assert fake.last_audio_path.endswith('.mp4')

    whisper_service.transcribe_audio(b'x', content_type='audio/wav')
    assert fake.last_audio_path.endswith('.wav')

    whisper_service.transcribe_audio(b'x', content_type='audio/webm;codecs=opus')
    assert fake.last_audio_path.endswith('.webm')

    whisper_service.transcribe_audio(b'x', content_type='video/mp4')
    assert fake.last_audio_path.endswith('.mp4')

    whisper_service.transcribe_audio(b'x', content_type='video/webm')
    assert fake.last_audio_path.endswith('.webm')


def test_transcribe_propagates_model_error():
    import whisper_service
    fake = _FakeModel(raise_on_transcribe=RuntimeError('decode failed'))
    whisper_service.set_model_for_testing(fake)

    with pytest.raises(RuntimeError, match='decode failed'):
        whisper_service.transcribe_audio(b'bad-bytes', content_type='audio/webm')


def test_transcribe_strips_leading_trailing_whitespace():
    import whisper_service
    fake = _FakeModel(segments_text=['   padded   '])
    whisper_service.set_model_for_testing(fake)

    result = whisper_service.transcribe_audio(b'x', content_type='audio/webm')

    assert result['text'] == 'padded'


def test_transcribe_returns_segments_with_quality_fields():
    import whisper_service
    # Note: real whisper segments embed leading spaces; emulate the same.
    fake = _FakeModel(segments=[
        _FakeSegment('Lord open my eyes', start=0.0, end=2.1,
                     no_speech_prob=0.04, avg_logprob=-0.25),
        _FakeSegment(' to see your word.', start=2.1, end=3.8,
                     no_speech_prob=0.08, avg_logprob=-0.32),
    ])
    whisper_service.set_model_for_testing(fake)

    result = whisper_service.transcribe_audio(b'x', content_type='audio/webm')

    assert result['segment_count'] == 2
    assert len(result['segments']) == 2
    assert result['segments'][0]['text'] == 'Lord open my eyes'
    assert result['segments'][0]['no_speech_prob'] == pytest.approx(0.04)
    assert result['segments'][0]['avg_logprob'] == pytest.approx(-0.25)
    assert result['word_count'] == 8
    assert result['avg_no_speech'] == pytest.approx(0.06)
    assert result['quality'] == 'good'


def test_transcribe_marks_silent_audio():
    import whisper_service
    fake = _FakeModel(segments=[
        _FakeSegment('', no_speech_prob=0.95),
    ])
    whisper_service.set_model_for_testing(fake)
    result = whisper_service.transcribe_audio(b'x', content_type='audio/webm')
    assert result['quality'] == 'silent'


def test_transcribe_marks_degraded_low_confidence():
    import whisper_service
    fake = _FakeModel(segments=[
        _FakeSegment('mumble', no_speech_prob=0.2, avg_logprob=-1.5),
    ])
    whisper_service.set_model_for_testing(fake)
    result = whisper_service.transcribe_audio(b'x', content_type='audio/webm')
    assert result['quality'] == 'degraded'


def test_transcribe_zero_segments_is_silent():
    import whisper_service
    fake = _FakeModel(segments=[])
    whisper_service.set_model_for_testing(fake)
    result = whisper_service.transcribe_audio(b'x', content_type='audio/webm')
    assert result['segment_count'] == 0
    assert result['quality'] == 'silent'
    assert result['text'] == ''
