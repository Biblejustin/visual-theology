"""Local speech-to-text using faster-whisper.

Audio bytes in, transcribed text + per-segment quality metadata out. The
model is lazy-loaded on first real call so ``import whisper_service`` is
cheap (app startup doesn't block on the model download). Tests inject a
fake model via ``set_model_for_testing``.
"""
import math
import os
import tempfile
import threading

_MODEL = None
_MODEL_LOCK = threading.Lock()
_MODEL_SIZE = os.environ.get('WHISPER_MODEL', 'medium')
_MODEL_DEVICE = os.environ.get('WHISPER_DEVICE', 'cpu')
_MODEL_COMPUTE = os.environ.get('WHISPER_COMPUTE_TYPE', 'int8')


def _suffix_for(content_type):
    """Pick a file suffix Whisper/ffmpeg will recognize from the browser MIME type."""
    ct = (content_type or '').lower().split(';')[0].strip()
    mapping = {
        'audio/webm': '.webm',
        'audio/ogg': '.ogg',
        'audio/mp4': '.mp4',
        'audio/m4a': '.m4a',
        'audio/mpeg': '.mp3',
        'audio/mp3': '.mp3',
        'audio/wav': '.wav',
        'audio/x-wav': '.wav',
        'video/mp4': '.mp4',
        'video/webm': '.webm',
    }
    return mapping.get(ct, '.webm')


def _get_model():
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    with _MODEL_LOCK:
        if _MODEL is not None:
            return _MODEL
        from faster_whisper import WhisperModel
        _MODEL = WhisperModel(_MODEL_SIZE, device=_MODEL_DEVICE, compute_type=_MODEL_COMPUTE)
    return _MODEL


def set_model_for_testing(model):
    """Inject a fake model (test hook)."""
    global _MODEL
    _MODEL = model


def _reset_for_testing():
    global _MODEL
    _MODEL = None


def _quality_bucket(avg_no_speech, avg_logprob, segment_count):
    """Bin transcription quality so the UI can show a meaningful badge.

    'good'     — confident speech detection across segments
    'degraded' — model uncertain on speech presence or low logprob
    'silent'   — high no_speech_prob across the recording (likely silence/junk)
    """
    if segment_count == 0:
        return 'silent'
    if avg_no_speech >= 0.6:
        return 'silent'
    if avg_logprob is None:
        return 'degraded' if avg_no_speech >= 0.35 else 'good'
    if avg_logprob <= -1.0 or avg_no_speech >= 0.35:
        return 'degraded'
    return 'good'


def transcribe_audio(audio_bytes, content_type='audio/webm'):
    """Transcribe an audio blob. Returns a rich dict suitable for UX display:

    ::
        {
          'text':           full transcript joined,
          'duration_sec':   audio duration as detected by whisper,
          'language':       detected ISO code (e.g., 'en'),
          'segments':       [{'start','end','text','no_speech_prob','avg_logprob'}],
          'segment_count':  number of segments,
          'word_count':     whitespace-separated word count of the joined text,
          'avg_no_speech':  mean no_speech_prob across segments (lower = clearer speech),
          'avg_logprob':    mean avg_logprob across segments (less-negative = higher confidence),
          'quality':        'good' | 'degraded' | 'silent',
        }
    """
    if not audio_bytes:
        raise ValueError('audio_bytes is empty')

    suffix = _suffix_for(content_type)
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        tmp.write(audio_bytes)
        tmp.close()
        model = _get_model()
        segments_iter, info = model.transcribe(tmp.name, beam_size=5, vad_filter=True)
        # Realize the iterator so we can compute aggregates across segments.
        segments = list(segments_iter)
        seg_dicts = []
        no_speech_vals = []
        logprob_vals = []
        for s in segments:
            no_speech = float(getattr(s, 'no_speech_prob', 0.0) or 0.0)
            avg_logprob = getattr(s, 'avg_logprob', None)
            seg_dicts.append({
                'start': float(getattr(s, 'start', 0.0) or 0.0),
                'end':   float(getattr(s, 'end', 0.0) or 0.0),
                'text':  (getattr(s, 'text', '') or '').strip(),
                'no_speech_prob': no_speech,
                'avg_logprob': (float(avg_logprob) if avg_logprob is not None else None),
            })
            no_speech_vals.append(no_speech)
            if avg_logprob is not None and not math.isnan(float(avg_logprob)):
                logprob_vals.append(float(avg_logprob))
        text = ''.join(s.text for s in segments).strip()
        avg_no_speech = (sum(no_speech_vals) / len(no_speech_vals)) if no_speech_vals else 0.0
        avg_logprob = (sum(logprob_vals) / len(logprob_vals)) if logprob_vals else None
        word_count = len(text.split()) if text else 0
        quality = _quality_bucket(avg_no_speech, avg_logprob, len(segments))
        return {
            'text': text,
            'duration_sec': float(getattr(info, 'duration', 0.0)),
            'language': getattr(info, 'language', '') or '',
            'segments': seg_dicts,
            'segment_count': len(segments),
            'word_count': word_count,
            'avg_no_speech': avg_no_speech,
            'avg_logprob': avg_logprob,
            'quality': quality,
        }
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
