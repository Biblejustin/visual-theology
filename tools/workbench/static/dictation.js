/**
 * dictation.js — Hybrid voice-to-text for /study with prominent recording UX.
 *
 * Layers:
 *   1. Live phase: Web Speech API streams interim text into the target textarea
 *      AND into the visible "recording stage" overlay so Bryan can see it land.
 *   2. Audio phase: MediaRecorder captures full audio; AnalyserNode drives a
 *      live waveform on the stage canvas so the UI proves the mic is hearing.
 *   3. Final phase: audio is POSTed to /study/session/<id>/transcribe; Whisper
 *      returns text + segment-level quality metadata (quality bucket, no_speech
 *      probability, language). The stage flips to a "review" view: duration,
 *      word count, language, quality badge, full transcript.
 *   4. Review buttons:
 *        - Keep         → writes the transcript to the target and closes stage
 *        - Discard      → restores the target's pre-recording value and closes
 *        - Re-record    → restores pre-recording value AND starts a new recording
 *
 * The stage is shared across all dictation instances on the page (singleton
 * DOM). Only one recording can be active at a time.
 */

'use strict';

(function () {
    var STORAGE_KEY = 'logos4.dictation.deviceId';

    function hasRecorderSupport() {
        return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);
    }

    function getSpeechRecognitionCtor() {
        return window.SpeechRecognition || window.webkitSpeechRecognition || null;
    }

    function pickMimeType() {
        var candidates = ['audio/mp4', 'audio/webm;codecs=opus', 'audio/webm', 'audio/ogg'];
        for (var i = 0; i < candidates.length; i++) {
            if (MediaRecorder.isTypeSupported(candidates[i])) return candidates[i];
        }
        return '';
    }

    function getAudioConstraints(deviceId) {
        if (!deviceId) return { audio: true };
        return { audio: { deviceId: deviceId } };
    }

    function shouldRetryWithDefaultMic(err) {
        if (!err) return false;
        var name = err.name || '';
        var message = err.message || '';
        return name === 'OverconstrainedError' ||
            name === 'ConstraintNotSatisfiedError' ||
            name === 'TypeError' ||
            /invalid constraint/i.test(message);
    }

    function setState(wrap, state, label) {
        wrap.classList.remove('state-idle', 'state-starting', 'state-recording', 'state-processing', 'state-error');
        wrap.classList.add('state-' + state);
        var statusEl = wrap.querySelector('.mic-status');
        var readout = wrap.querySelector('.mic-readout');
        var readoutStatus = wrap.querySelector('.mic-readout-status');
        var text = '';
        if (label) text = label;
        else if (state === 'starting') text = 'Starting...';
        else if (state === 'recording') text = 'Recording';
        else if (state === 'processing') text = 'Transcribing';
        else if (state === 'error') text = 'Error';
        if (statusEl) statusEl.textContent = text;
        if (readoutStatus) readoutStatus.textContent = text || 'Idle';
        if (readout) readout.hidden = state === 'idle';
    }

    function setLevel(wrap, ratio) {
        var pct = Math.max(2, Math.min(100, Math.round((ratio || 0) * 100)));
        var bars = wrap.querySelectorAll('.mic-level span, .mic-readout-meter span');
        bars.forEach(function (bar) { bar.style.width = pct + '%'; });
    }

    function clearChildren(node) {
        while (node.firstChild) node.removeChild(node.firstChild);
    }

    function populatePicker(wrap) {
        var select = wrap.querySelector('.mic-picker-select');
        if (!select) return Promise.resolve();
        return navigator.mediaDevices.enumerateDevices().then(function (devices) {
            var stored = localStorage.getItem(STORAGE_KEY) || '';
            clearChildren(select);
            var audioInputs = devices.filter(function (d) { return d.kind === 'audioinput'; });
            if (!audioInputs.length) {
                var empty = document.createElement('option');
                empty.value = '';
                empty.textContent = 'No microphone found';
                select.appendChild(empty);
                return;
            }
            var sysOpt = document.createElement('option');
            sysOpt.value = '';
            sysOpt.textContent = 'System default';
            select.appendChild(sysOpt);
            audioInputs.forEach(function (d, i) {
                var opt = document.createElement('option');
                opt.value = d.deviceId;
                opt.textContent = d.label || ('Microphone ' + (i + 1));
                select.appendChild(opt);
            });
            select.value = stored;
        });
    }

    function bindPicker(wrap) {
        var toggle = wrap.querySelector('.mic-picker-toggle');
        var picker = wrap.querySelector('.mic-picker');
        var select = wrap.querySelector('.mic-picker-select');
        if (!toggle || !picker || !select) return;
        function openPicker() {
            populatePicker(wrap);
            picker.hidden = false;
            toggle.setAttribute('aria-expanded', 'true');
            wrap.classList.add('picker-open');
        }
        function closePicker() {
            picker.hidden = true;
            toggle.setAttribute('aria-expanded', 'false');
            wrap.classList.remove('picker-open');
        }
        toggle.addEventListener('click', function (e) {
            e.preventDefault(); e.stopPropagation();
            if (picker.hidden) openPicker(); else closePicker();
        });
        select.addEventListener('change', function () {
            localStorage.setItem(STORAGE_KEY, select.value);
            closePicker();
        });
        picker.addEventListener('click', function (e) { e.stopPropagation(); });
        document.addEventListener('click', function (e) {
            if (!wrap.contains(e.target)) closePicker();
        });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && !picker.hidden) closePicker();
        });
    }

    // ── Recording stage (singleton overlay) ──────────────────────────────

    var Stage = {
        el: null, canvas: null, ctx: null,
        statusEl: null, timeEl: null, levelEl: null, transcriptEl: null,
        stopBtn: null, reviewEl: null,
        owner: null,                  // current Dictation instance using the stage
        waveformBuf: null,            // ring buffer of recent level samples (0..1)
        startedAt: 0,                 // ms timestamp when recording started
        tickHandle: null,
    };

    function ensureStage() {
        if (Stage.el) return Stage.el;

        var el = document.createElement('div');
        el.id = 'dictation-stage';
        el.setAttribute('aria-live', 'polite');
        el.hidden = true;
        el.innerHTML = ''
          + '<div class="ds-card">'
          +   '<div class="ds-header">'
          +     '<span class="ds-dot"></span>'
          +     '<span class="ds-status">Recording</span>'
          +     '<span class="ds-time" aria-label="Elapsed">0:00</span>'
          +     '<button type="button" class="ds-stop" aria-label="Stop recording">Stop</button>'
          +   '</div>'
          +   '<canvas class="ds-canvas" width="800" height="60" aria-hidden="true"></canvas>'
          +   '<div class="ds-meter" aria-hidden="true"><span></span></div>'
          +   '<div class="ds-transcript-label">Live transcript</div>'
          +   '<div class="ds-transcript" aria-live="polite">Listening…</div>'
          +   '<div class="ds-review" hidden></div>'
          + '</div>';

        var style = document.createElement('style');
        style.textContent = ''
          + '#dictation-stage {'
          + '  position: fixed; top: 0; left: 0; right: 0; z-index: 5000;'
          + '  background: rgba(20, 18, 14, 0.92); backdrop-filter: blur(8px);'
          + '  border-bottom: 2px solid #c9b88a; box-shadow: 0 4px 20px rgba(0,0,0,0.5);'
          + '  font-family: -apple-system, system-ui, sans-serif; color: #e8e1d0;'
          + '  padding: 14px 24px; transition: opacity 0.15s ease;'
          + '}'
          + '#dictation-stage .ds-card { max-width: 920px; margin: 0 auto; }'
          + '#dictation-stage .ds-header {'
          + '  display: flex; align-items: center; gap: 12px; margin-bottom: 8px;'
          + '  font-size: 14px;'
          + '}'
          + '#dictation-stage .ds-dot {'
          + '  width: 11px; height: 11px; border-radius: 50%;'
          + '  background: #d04848; box-shadow: 0 0 6px #d04848;'
          + '  animation: ds-pulse 1.1s infinite;'
          + '}'
          + '@keyframes ds-pulse {'
          + '  0%, 100% { opacity: 1; transform: scale(1); }'
          + '  50%      { opacity: 0.45; transform: scale(0.82); }'
          + '}'
          + '#dictation-stage .ds-status { font-weight: 600; letter-spacing: 0.02em; }'
          + '#dictation-stage .ds-time {'
          + '  font-variant-numeric: tabular-nums; color: #c9b88a;'
          + '  margin-left: auto; margin-right: 12px; font-size: 13px;'
          + '}'
          + '#dictation-stage .ds-stop {'
          + '  background: #c9b88a; color: #1a1814; border: none;'
          + '  padding: 7px 18px; border-radius: 4px; font-size: 13px;'
          + '  font-weight: 600; cursor: pointer;'
          + '}'
          + '#dictation-stage .ds-stop:hover { background: #ebd9a7; }'
          + '#dictation-stage .ds-canvas {'
          + '  display: block; width: 100%; height: 60px; background: #15130f;'
          + '  border: 1px solid #2d2a26; border-radius: 4px;'
          + '}'
          + '#dictation-stage .ds-meter {'
          + '  margin-top: 6px; height: 4px; background: #2d2a26; border-radius: 2px;'
          + '  overflow: hidden;'
          + '}'
          + '#dictation-stage .ds-meter span {'
          + '  display: block; height: 100%; width: 2%; background: #88b878;'
          + '  transition: width 0.06s linear, background 0.2s linear;'
          + '}'
          + '#dictation-stage .ds-transcript-label {'
          + '  font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em;'
          + '  color: #8a8275; margin: 10px 0 4px;'
          + '}'
          + '#dictation-stage .ds-transcript {'
          + '  min-height: 38px; max-height: 120px; overflow-y: auto;'
          + '  font-size: 15px; line-height: 1.45; color: #e8e1d0;'
          + '  background: #1a1814; border: 1px solid #2d2a26;'
          + '  border-radius: 4px; padding: 8px 12px;'
          + '}'
          + '#dictation-stage .ds-transcript.empty { color: #5a5447; font-style: italic; }'
          + '#dictation-stage .ds-review {'
          + '  margin-top: 10px; padding-top: 10px; border-top: 1px solid #2d2a26;'
          + '}'
          + '#dictation-stage .ds-summary {'
          + '  display: flex; gap: 14px; flex-wrap: wrap; align-items: baseline;'
          + '  font-size: 13px; color: #b8af9d; margin-bottom: 8px;'
          + '}'
          + '#dictation-stage .ds-summary .badge {'
          + '  padding: 2px 10px; border-radius: 999px; font-size: 11px;'
          + '  font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em;'
          + '}'
          + '#dictation-stage .ds-summary .badge.good     { background: #1f3a2d; color: #88e878; }'
          + '#dictation-stage .ds-summary .badge.degraded { background: #3d3a1f; color: #ebd078; }'
          + '#dictation-stage .ds-summary .badge.silent   { background: #4d2a2a; color: #ebc9c9; }'
          + '#dictation-stage .ds-summary .stat { font-variant-numeric: tabular-nums; }'
          + '#dictation-stage .ds-actions {'
          + '  display: flex; gap: 10px; margin-top: 6px;'
          + '}'
          + '#dictation-stage .ds-actions button {'
          + '  background: transparent; color: #c9b88a; border: 1px solid #6a5a3a;'
          + '  padding: 6px 14px; border-radius: 4px; font-size: 13px;'
          + '  font-weight: 500; cursor: pointer;'
          + '}'
          + '#dictation-stage .ds-actions button.primary {'
          + '  background: #c9b88a; color: #1a1814; border-color: #c9b88a;'
          + '}'
          + '#dictation-stage .ds-actions button:hover { background: #6a5a3a; color: #e8e1d0; }'
          + '#dictation-stage .ds-actions button.primary:hover { background: #ebd9a7; color: #1a1814; }'
          + '#dictation-stage.body-doubling, body.body-doubling #dictation-stage { opacity: 0.85; }';
        document.head.appendChild(style);
        document.body.appendChild(el);

        Stage.el = el;
        Stage.canvas = el.querySelector('.ds-canvas');
        Stage.ctx = Stage.canvas.getContext('2d');
        Stage.statusEl = el.querySelector('.ds-status');
        Stage.timeEl = el.querySelector('.ds-time');
        Stage.levelEl = el.querySelector('.ds-meter span');
        Stage.transcriptEl = el.querySelector('.ds-transcript');
        Stage.stopBtn = el.querySelector('.ds-stop');
        Stage.reviewEl = el.querySelector('.ds-review');
        Stage.waveformBuf = new Float32Array(160);

        Stage.stopBtn.addEventListener('click', function () {
            if (Stage.owner) Stage.owner.stop();
        });
        return el;
    }

    function showStage(owner) {
        ensureStage();
        Stage.owner = owner;
        Stage.el.hidden = false;
        // Signal to study.js's auto-pause: while we're recording, count it as activity.
        window.dictationActive = true;
        Stage.startedAt = Date.now();
        Stage.statusEl.textContent = 'Recording';
        Stage.timeEl.textContent = '0:00';
        // Silent phases (prayer / translation / digest) get a context-aware
        // placeholder that names the heart-and-devotion frame and tells
        // Bryan the transcript arrives at the end. No live-text pressure.
        if (owner && owner.suppressLiveTranscript) {
            var phaseLabel = (owner.phase || '').replace(/_/g, ' ');
            Stage.transcriptEl.textContent =
              'Recording your ' + (phaseLabel || 'session') +
              '. Transcript will appear when you stop — pray / read / think freely.';
        } else {
            Stage.transcriptEl.textContent = 'Listening…';
        }
        Stage.transcriptEl.classList.add('empty');
        Stage.reviewEl.hidden = true;
        Stage.reviewEl.innerHTML = '';
        Stage.stopBtn.hidden = false;
        Stage.stopBtn.disabled = false;
        Stage.stopBtn.textContent = 'Stop';
        for (var i = 0; i < Stage.waveformBuf.length; i++) Stage.waveformBuf[i] = 0;
        drawWaveform(0);
        startStageTicker();
    }

    function hideStage() {
        if (!Stage.el) return;
        Stage.el.hidden = true;
        Stage.owner = null;
        window.dictationActive = false;
        stopStageTicker();
    }

    function startStageTicker() {
        stopStageTicker();
        Stage.tickHandle = window.setInterval(function () {
            var elapsedMs = Date.now() - Stage.startedAt;
            var sec = Math.floor(elapsedMs / 1000);
            var mm = Math.floor(sec / 60);
            var ss = sec % 60;
            Stage.timeEl.textContent = mm + ':' + (ss < 10 ? '0' : '') + ss;
        }, 250);
    }

    function stopStageTicker() {
        if (Stage.tickHandle) {
            window.clearInterval(Stage.tickHandle);
            Stage.tickHandle = null;
        }
    }

    function pushLevelToStage(ratio) {
        if (!Stage.el || Stage.el.hidden) return;
        var clamped = Math.max(0, Math.min(1, ratio));
        // Rolling buffer
        var buf = Stage.waveformBuf;
        for (var i = 0; i < buf.length - 1; i++) buf[i] = buf[i + 1];
        buf[buf.length - 1] = clamped;
        drawWaveform(clamped);
        if (Stage.levelEl) {
            var pct = Math.max(2, Math.min(100, Math.round(clamped * 100)));
            Stage.levelEl.style.width = pct + '%';
            // Tint the bar by level so the green→yellow→red mapping is obvious.
            var color = clamped > 0.85 ? '#d04848' : (clamped > 0.5 ? '#ebd078' : '#88b878');
            Stage.levelEl.style.background = color;
        }
    }

    function drawWaveform(latestRatio) {
        if (!Stage.canvas) return;
        var canvas = Stage.canvas;
        // Match display size (devicePixelRatio support deferred — visual is fine).
        var w = canvas.clientWidth || canvas.width;
        var h = canvas.height;
        if (canvas.width !== w) canvas.width = w;
        var ctx = Stage.ctx;
        ctx.clearRect(0, 0, w, h);

        var buf = Stage.waveformBuf;
        var n = buf.length;
        var barW = Math.max(2, Math.floor(w / n) - 1);
        var gap = 1;
        var totalBarPlusGap = barW + gap;
        var startX = w - n * totalBarPlusGap;
        if (startX < 0) startX = 0;

        for (var i = 0; i < n; i++) {
            var v = buf[i];
            var amp = Math.max(2, v * h);
            var x = startX + i * totalBarPlusGap;
            var y = (h - amp) / 2;
            var alpha = 0.35 + 0.65 * (i / n);
            var fg = v > 0.85 ? [208, 72, 72] : v > 0.5 ? [235, 208, 120] : [136, 184, 120];
            ctx.fillStyle = 'rgba(' + fg.join(',') + ',' + alpha.toFixed(2) + ')';
            ctx.fillRect(x, y, barW, amp);
        }
    }

    function setStageTranscript(text) {
        if (!Stage.transcriptEl) return;
        var trimmed = (text || '').trim();
        if (!trimmed) {
            Stage.transcriptEl.textContent = 'Listening…';
            Stage.transcriptEl.classList.add('empty');
            return;
        }
        Stage.transcriptEl.textContent = trimmed;
        Stage.transcriptEl.classList.remove('empty');
        // Auto-scroll to bottom so the latest words are visible.
        Stage.transcriptEl.scrollTop = Stage.transcriptEl.scrollHeight;
    }

    function setStageProcessing() {
        if (!Stage.el) return;
        Stage.statusEl.textContent = 'Transcribing…';
        Stage.stopBtn.disabled = true;
        Stage.stopBtn.textContent = 'Processing';
    }

    function setStageReview(data, owner) {
        if (!Stage.el) return;
        stopStageTicker();
        Stage.statusEl.textContent = 'Review';
        Stage.stopBtn.hidden = true;
        var text = (data.text || '').trim();
        var quality = data.quality || (text ? 'degraded' : 'silent');
        var dur = Number(data.duration_sec || 0);
        var words = Number(data.word_count || (text ? text.split(/\s+/).length : 0));
        var lang = data.language || '?';

        if (text) {
            Stage.transcriptEl.textContent = text;
            Stage.transcriptEl.classList.remove('empty');
        } else {
            Stage.transcriptEl.textContent = 'No words recognized.';
            Stage.transcriptEl.classList.add('empty');
        }

        var review = Stage.reviewEl;
        review.innerHTML = '';
        var summary = document.createElement('div');
        summary.className = 'ds-summary';
        summary.innerHTML = ''
          + '<span class="badge ' + quality + '">' + quality + '</span>'
          + '<span class="stat">' + dur.toFixed(1) + 's recorded</span>'
          + '<span class="stat">' + words + ' words</span>'
          + '<span class="stat">lang ' + lang + '</span>'
          + '<span class="stat">' + (data.segment_count || 0) + ' segments</span>';
        review.appendChild(summary);

        var actions = document.createElement('div');
        actions.className = 'ds-actions';

        if (text) {
            var keepBtn = document.createElement('button');
            keepBtn.type = 'button';
            keepBtn.className = 'primary';
            keepBtn.textContent = 'Keep transcript';
            keepBtn.addEventListener('click', function () { owner.acceptTranscript(text); });
            actions.appendChild(keepBtn);
        }

        var retryBtn = document.createElement('button');
        retryBtn.type = 'button';
        retryBtn.textContent = text ? 'Discard & re-record' : 'Re-record';
        retryBtn.addEventListener('click', function () { owner.discardAndRetry(); });
        actions.appendChild(retryBtn);

        if (text) {
            var discardBtn = document.createElement('button');
            discardBtn.type = 'button';
            discardBtn.textContent = 'Discard';
            discardBtn.addEventListener('click', function () { owner.discardAndClose(); });
            actions.appendChild(discardBtn);
        } else {
            var closeBtn = document.createElement('button');
            closeBtn.type = 'button';
            closeBtn.textContent = 'Close';
            closeBtn.addEventListener('click', function () { owner.discardAndClose(); });
            actions.appendChild(closeBtn);
        }

        review.appendChild(actions);
        review.hidden = false;
    }

    // ── Dictation instance ───────────────────────────────────────────────

    // Silent phases per v2 spec §4 — prayer / read_translate / digestion are
    // heart-and-devotion phases where the LLM does not engage. For dictation,
    // we still RECORD the audio but suppress the live transcript display so
    // Bryan can pray without prose interrupting. Whisper still transcribes on
    // stop so the captured text is available.
    var SILENT_PHASES = {
        prayer: true,
        read_translate: true,
        text_work: true,
        translation: true,
        read_text: true,
        digestion: true,
    };

    function Dictation(wrap) {
        this.wrap = wrap;
        this.btn = wrap.querySelector('.mic-button');
        this.targetSelector = wrap.getAttribute('data-target');
        this.sessionId = wrap.getAttribute('data-session-id');
        this.phase = wrap.getAttribute('data-phase') || '';
        this.suppressLiveTranscript = !!SILENT_PHASES[this.phase];
        this.recorder = null;
        this.stream = null;
        this.chunks = [];
        this.recording = false;
        this.recognition = null;
        this.previewStart = null;
        this.previewLen = 0;
        this.processing = false;
        this.audioContext = null;
        this.analyser = null;
        this.levelData = null;
        this.levelRaf = null;
        this.preDictationValue = '';   // target's value before this session
        this.preDictationStart = 0;    // cursor position before this session

        var self = this;
        this.btn.addEventListener('click', function () { self.toggle(); });
        bindPicker(wrap);
        setState(wrap, 'idle');
    }

    Dictation.prototype.getTarget = function () {
        return document.querySelector(this.targetSelector);
    };

    Dictation.prototype.toggle = function () {
        if (!hasRecorderSupport()) {
            setState(this.wrap, 'error');
            alert('Dictation is not supported in this browser.');
            return;
        }
        if (this.processing) return;
        if (this.recording) this.stop();
        else this.start();
    };

    Dictation.prototype.writePreview = function (text) {
        var target = this.getTarget();
        if (!target || this.previewStart == null) return;
        text = text || '';
        var val = target.value;
        var before = val.slice(0, this.previewStart);
        var after = val.slice(this.previewStart + this.previewLen);
        var needsSpace = text && before.length > 0 && !/\s$/.test(before) && !/^\s/.test(text);
        var injected = (needsSpace ? ' ' : '') + text;
        target.value = before + injected + after;
        this.previewLen = injected.length;
        var pos = this.previewStart + this.previewLen;
        try { target.setSelectionRange(pos, pos); } catch (e) { /* non-text inputs */ }
        target.dispatchEvent(new Event('input', { bubbles: true }));
    };

    Dictation.prototype.startLivePreview = function () {
        var SR = getSpeechRecognitionCtor();
        if (!SR) {
            setState(this.wrap, 'recording', 'Recording - stop for transcript');
            setStageTranscript('Listening… (live preview unavailable in this browser; full transcript on stop)');
            return false;
        }
        var target = this.getTarget();
        if (!target) return false;

        var start = (typeof target.selectionStart === 'number') ? target.selectionStart : target.value.length;
        this.previewStart = start;
        this.previewLen = 0;

        var self = this;
        var rec = new SR();
        rec.continuous = true;
        rec.interimResults = true;
        rec.lang = 'en-US';

        rec.onresult = function (e) {
            var text = '';
            for (var i = 0; i < e.results.length; i++) {
                text += e.results[i][0].transcript;
            }
            setState(self.wrap, 'recording', 'Live text ready');
            // Silent-phase recordings (prayer / translation / digest) suppress
            // BOTH the textarea preview AND the on-stage transcript so Bryan
            // can pray without prose interrupting. Whisper still runs on stop.
            if (self.suppressLiveTranscript) return;
            self.writePreview(text);
            setStageTranscript(text);
        };

        rec.onerror = function (e) {
            console.warn('dictation: SpeechRecognition error', e.error);
            if (self.recording) setState(self.wrap, 'recording', 'Recording - stop for transcript');
        };

        rec.onend = function () {
            if (self.recording && self.recognition === rec) {
                try { rec.start(); } catch (err) { /* already started / stopped */ }
            }
        };

        try {
            rec.start();
            this.recognition = rec;
            setState(this.wrap, 'recording', 'Live text ready');
            return true;
        } catch (err) {
            console.warn('dictation: SpeechRecognition.start failed', err);
            this.recognition = null;
            setState(this.wrap, 'recording', 'Recording - stop for transcript');
            return false;
        }
    };

    Dictation.prototype.stopLivePreview = function () {
        if (!this.recognition) return;
        try {
            this.recognition.onend = null;
            this.recognition.stop();
        } catch (e) { /* ignore */ }
        this.recognition = null;
    };

    Dictation.prototype.startLevelMeter = function (stream) {
        this.stopLevelMeter();
        var AC = window.AudioContext || window.webkitAudioContext;
        if (!AC) { setLevel(this.wrap, 0); return; }
        try {
            var ctx = new AC();
            var analyser = ctx.createAnalyser();
            analyser.fftSize = 256;
            ctx.createMediaStreamSource(stream).connect(analyser);
            this.audioContext = ctx;
            this.analyser = analyser;
            this.levelData = new Uint8Array(analyser.frequencyBinCount);
            if (ctx.state === 'suspended' && ctx.resume) ctx.resume();
            this.tickLevelMeter();
        } catch (err) {
            console.warn('dictation: audio level meter failed', err);
            setLevel(this.wrap, 0);
        }
    };

    Dictation.prototype.tickLevelMeter = function () {
        if (!this.analyser || !this.levelData) return;
        this.analyser.getByteTimeDomainData(this.levelData);
        var sum = 0;
        for (var i = 0; i < this.levelData.length; i++) {
            var centered = this.levelData[i] - 128;
            sum += centered * centered;
        }
        var rms = Math.sqrt(sum / this.levelData.length) / 128;
        var amplified = Math.min(1, rms * 8);
        setLevel(this.wrap, amplified);
        pushLevelToStage(amplified);
        var self = this;
        this.levelRaf = window.requestAnimationFrame(function () { self.tickLevelMeter(); });
    };

    Dictation.prototype.stopLevelMeter = function () {
        if (this.levelRaf) {
            window.cancelAnimationFrame(this.levelRaf);
            this.levelRaf = null;
        }
        if (this.audioContext) {
            try { this.audioContext.close(); } catch (e) { /* ignore */ }
        }
        this.audioContext = null;
        this.analyser = null;
        this.levelData = null;
        setLevel(this.wrap, 0);
    };

    Dictation.prototype.start = function () {
        var self = this;
        var deviceId = localStorage.getItem(STORAGE_KEY) || '';
        setState(this.wrap, 'starting', 'Starting...');
        setLevel(this.wrap, 0);

        // Snapshot the target so Discard / Re-record can roll back to it.
        var tgt = this.getTarget();
        this.preDictationValue = tgt ? tgt.value : '';
        this.preDictationStart = tgt ? ((typeof tgt.selectionStart === 'number') ? tgt.selectionStart : tgt.value.length) : 0;
        this.previewStart = this.preDictationStart;
        this.previewLen = 0;

        showStage(this);

        function openMic(constraints) {
            return navigator.mediaDevices.getUserMedia(constraints);
        }

        openMic(getAudioConstraints(deviceId)).catch(function (err) {
            if (!deviceId || !shouldRetryWithDefaultMic(err)) throw err;
            console.warn('dictation: selected microphone failed, retrying with system default', err);
            localStorage.removeItem(STORAGE_KEY);
            return openMic(getAudioConstraints(''));
        }).then(function (stream) {
            populatePicker(self.wrap);

            var mime = pickMimeType();
            self.stream = stream;
            self.chunks = [];
            self.recorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);

            self.recorder.ondataavailable = function (e) {
                if (e.data && e.data.size > 0) self.chunks.push(e.data);
            };
            self.recorder.onstop = function () { self.finalize(); };
            self.recorder.onerror = function (e) { console.warn('dictation: MediaRecorder error', e); };

            self.recorder.start();
            self.recording = true;
            self.processing = false;
            setState(self.wrap, 'recording', 'Recording - click mic to stop');
            self.startLevelMeter(stream);
            self.startLivePreview();
        }).catch(function (err) {
            console.error('dictation: getUserMedia failed', err);
            setState(self.wrap, 'error');
            hideStage();
            alert('Microphone access was denied or unavailable: ' + err.message);
        });
    };

    Dictation.prototype.stop = function () {
        this.stopLivePreview();
        if (this.recorder && this.recording) {
            try { this.recorder.stop(); } catch (e) { /* already stopped */ }
            this.recording = false;
            this.processing = true;
            setState(this.wrap, 'processing', 'Transcribing...');
            setStageProcessing();
        }
        this.stopLevelMeter();
        if (this.stream) {
            this.stream.getTracks().forEach(function (t) { t.stop(); });
            this.stream = null;
        }
    };

    Dictation.prototype.finalize = function () {
        var self = this;
        var blob = new Blob(this.chunks, { type: (this.recorder && this.recorder.mimeType) || 'audio/webm' });
        this.chunks = [];
        if (!blob.size) {
            this.processing = false;
            setState(this.wrap, 'error', 'No audio captured');
            setStageReview({ text: '', duration_sec: 0, quality: 'silent', word_count: 0, segment_count: 0 }, this);
            return;
        }

        var form = new FormData();
        var ext = (blob.type.indexOf('mp4') >= 0) ? 'mp4' : (blob.type.indexOf('ogg') >= 0) ? 'ogg' : 'webm';
        form.append('audio', blob, 'dictation.' + ext);

        var url = '/study/session/' + this.sessionId + '/transcribe';
        fetch(url, { method: 'POST', body: form })
            .then(function (r) {
                if (!r.ok) return r.json().then(function (j) { throw new Error(j.error || ('HTTP ' + r.status)); });
                return r.json();
            })
            .then(function (data) {
                self.processing = false;
                setState(self.wrap, 'idle');
                setStageReview(data, self);
            })
            .catch(function (err) {
                console.error('dictation: Whisper transcription failed', err);
                self.processing = false;
                setState(self.wrap, 'error');
                setStageReview({
                    text: '',
                    duration_sec: (Date.now() - Stage.startedAt) / 1000,
                    quality: 'silent',
                    word_count: 0,
                    segment_count: 0,
                    language: '',
                }, self);
                alert('Whisper transcription failed: ' + err.message);
            });
    };

    Dictation.prototype._rollbackTarget = function () {
        var target = this.getTarget();
        if (!target) return;
        target.value = this.preDictationValue;
        try { target.setSelectionRange(this.preDictationStart, this.preDictationStart); } catch (e) { /* ignore */ }
        target.dispatchEvent(new Event('input', { bubbles: true }));
    };

    Dictation.prototype.acceptTranscript = function (text) {
        // Roll back the live-preview / interim text, then insert the final
        // Whisper transcript at the original cursor position.
        this._rollbackTarget();
        this.previewStart = this.preDictationStart;
        this.previewLen = 0;
        this.writePreview(text);
        this.previewStart = null;
        this.previewLen = 0;
        hideStage();
    };

    Dictation.prototype.discardAndClose = function () {
        this._rollbackTarget();
        this.previewStart = null;
        this.previewLen = 0;
        hideStage();
    };

    Dictation.prototype.discardAndRetry = function () {
        this._rollbackTarget();
        this.previewStart = null;
        this.previewLen = 0;
        // Hide first to reset the stage cleanly, then start a fresh recording.
        hideStage();
        var self = this;
        // Defer to next tick so the hide animation / cleanup completes.
        window.setTimeout(function () { self.start(); }, 50);
    };

    function init() {
        if (!hasRecorderSupport()) return;
        var wraps = document.querySelectorAll('[data-mic-button]');
        wraps.forEach(function (w) { new Dictation(w); });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
