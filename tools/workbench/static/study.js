/**
 * study.js — Hybrid card + conversation sermon study UI
 * Vanilla JS. No framework. SSE streaming. Outline sidebar. Session clock.
 */

'use strict';

/* ── Session Clock (counts UP) ──────────────────────────────────────────── */

var studyClock = {
    startTime: null,
    interval: null,
    elapsed: 0,
    paused: false,
    nudge2hShown: false,
    nudge4hShown: false,

    // Activity / visibility tracking (task #53 timer fix).
    lastActivityAt: 0,
    lastTickAt: 0,                           // wall-clock at the most recent tick
    autoPausedAt: 0,
    autoPausedReason: null,
    autoPausedElapsedSnapshot: 0,
    IDLE_THRESHOLD_MS: 5 * 60 * 1000,
    TAB_HIDDEN_THRESHOLD_MS: 30 * 1000,
    TICK_GAP_THRESHOLD_MS: 60 * 1000,        // > 60s between ticks = suspension
    GAP_NOTICE_MIN_SEC: 60,
    _listenersBound: false,

    start: function(initialElapsed) {
        this.elapsed = initialElapsed || 0;
        this.startTime = Date.now() - (this.elapsed * 1000);
        this.lastActivityAt = Date.now();
        this.lastTickAt = Date.now();
        this._bindActivityListeners();
        if (this.interval) return;
        var self = this;
        this.interval = setInterval(function() {
            var now = Date.now();
            // Tick-gap defense (task #72). The browser is supposed to fire
            // this interval every ~1s. If it fired more than 60s late, the
            // tab / system was suspended (laptop sleep, aggressive tab
            // throttling). The naive elapsed recompute below would inflate
            // by the full suspension duration. Rebase startTime so elapsed
            // stays at the snapshot we had pre-suspension, then auto-pause
            // so the gap notice can offer 'count it' if Bryan was actually
            // working in another app.
            if (self.lastTickAt && (now - self.lastTickAt) > self.TICK_GAP_THRESHOLD_MS) {
                self.startTime = now - (self.elapsed * 1000);
                self.lastActivityAt = self.lastTickAt;  // pre-suspension anchor for gap-notice math
                if (!self.paused) {
                    self.autoPausedAt = self.lastTickAt;
                    self.autoPausedElapsedSnapshot = self.elapsed;
                    self.autoPausedReason = 'tick_gap';
                    self.paused = true;
                    self.syncToServer();
                }
            } else if (!self.paused) {
                self.elapsed = Math.floor((now - self.startTime) / 1000);
                self.checkWellbeing();
                self._checkIdle();
            }
            self.lastTickAt = now;
            self.updateDisplay();
        }, 1000);
        this.updateDisplay();
    },

    _noteActivity: function() {
        this.lastActivityAt = Date.now();
        // Either kind of pause (idle OR system-suspend) auto-resumes on touch.
        if (this.paused &&
            (this.autoPausedReason === 'idle' || this.autoPausedReason === 'tick_gap')) {
            this._autoResume();
        }
    },

    _bindActivityListeners: function() {
        if (this._listenersBound) return;
        this._listenersBound = true;
        var self = this;
        ['keydown', 'mousedown', 'pointerdown', 'touchstart'].forEach(function(ev) {
            document.addEventListener(ev, function() { self._noteActivity(); }, { passive: true, capture: true });
        });
        var lastMove = 0;
        document.addEventListener('mousemove', function() {
            var now = Date.now();
            if (now - lastMove > 2000) { lastMove = now; self._noteActivity(); }
        }, { passive: true });
        document.addEventListener('scroll', function() { self._noteActivity(); }, { passive: true, capture: true });
        document.addEventListener('visibilitychange', function() {
            if (document.hidden) {
                self._scheduleTabHiddenPause();
            } else if (self.autoPausedReason === 'tab_hidden' && self.paused) {
                self._autoResume();
            }
        });
    },

    _scheduleTabHiddenPause: function() {
        var self = this;
        var snapshotHiddenAt = Date.now();
        window.setTimeout(function() {
            if (document.hidden &&
                !self.paused &&
                Date.now() - snapshotHiddenAt >= self.TAB_HIDDEN_THRESHOLD_MS - 100) {
                self._autoPause('tab_hidden');
            }
        }, this.TAB_HIDDEN_THRESHOLD_MS);
    },

    _checkIdle: function() {
        if (this.paused) return;
        // Mic recording counts as activity (no auto-pause while dictating).
        if (window.dictationActive) {
            this.lastActivityAt = Date.now();
            return;
        }
        if (Date.now() - this.lastActivityAt > this.IDLE_THRESHOLD_MS) {
            this._autoPause('idle');
        }
    },

    _autoPause: function(reason) {
        if (this.paused) return;
        this.paused = true;
        this.autoPausedAt = Date.now();
        this.autoPausedElapsedSnapshot = this.elapsed;
        this.autoPausedReason = reason;
        this.updateDisplay();
        this.syncToServer();
    },

    _autoResume: function() {
        var gapMs = Date.now() - this.autoPausedAt;
        var gapSec = Math.max(0, Math.floor(gapMs / 1000));
        var reason = this.autoPausedReason;
        this.autoPausedAt = 0;
        this.autoPausedReason = null;
        this.startTime = Date.now() - (this.elapsed * 1000);
        this.paused = false;
        this.lastActivityAt = Date.now();
        this.lastTickAt = Date.now();
        this.updateDisplay();
        if (gapSec >= this.GAP_NOTICE_MIN_SEC) {
            showGapNotice(reason, gapSec, this);
        }
    },

    countTheGap: function(gapSec) {
        this.elapsed += gapSec;
        this.startTime = Date.now() - (this.elapsed * 1000);
        this.updateDisplay();
        this.syncToServer();
    },

    toggle: function() {
        if (this.paused) {
            this.autoPausedAt = 0;
            this.autoPausedReason = null;
            // Resume — adjust startTime so elapsed stays correct
            this.startTime = Date.now() - (this.elapsed * 1000);
            this.paused = false;
            this.lastActivityAt = Date.now();
            this.lastTickAt = Date.now();
        } else {
            this.paused = true;
        }
        this.updateDisplay();
    },

    updateDisplay: function() {
        var el = document.getElementById('session-clock');
        if (el) {
            var text = this.formatDuration(this.elapsed);
            if (this.paused) {
                if (this.autoPausedReason === 'tab_hidden') text += ' (auto-paused: tab hidden)';
                else if (this.autoPausedReason === 'idle') text += ' (auto-paused: idle)';
                else if (this.autoPausedReason === 'tick_gap') text += ' (auto-paused: tab suspended)';
                else text += ' (paused)';
            }
            el.textContent = text;
            el.classList.toggle('paused', this.paused);
            el.classList.toggle('auto-paused', !!this.autoPausedReason);
        }
        // Broadcast clock state on <body> so the time-coach rings can dim/mute.
        if (this.paused) {
            var suffix = this.autoPausedReason || 'manual';
            document.body.dataset.clockState = 'paused-' + suffix;
        } else {
            delete document.body.dataset.clockState;
        }
    },

    formatDuration: function(totalSeconds) {
        var h = Math.floor(totalSeconds / 3600);
        var m = Math.floor((totalSeconds % 3600) / 60);
        if (h > 0) return h + 'h ' + m + 'm';
        return m + 'm';
    },

    checkWellbeing: function() {
        // Disabled. Bryan: "this little thing is annoying. it doesn't actually
        // work well with time in real life and I just dismiss it." Clock
        // elapsed accumulates wall-time, not actual focused work \u2014 even
        // with auto-pause it fires after 2h of "having the tab open"
        // rather than 2h of real engagement. Aligned with no-rescue-modes
        // design (see feedback_no_rescue_modes_choice).
    },

    syncToServer: function() {
        if (!window.studySessionId) return;
        fetch('/study/session/' + window.studySessionId + '/clock', {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ elapsed: this.elapsed })
        }).catch(function() {});
    }
};

/* ── Gap notice (auto-pause return prompt) ──────────────────────────────── */

function showGapNotice(reason, gapSec, clock) {
    var existing = document.getElementById('gap-notice');
    if (existing) existing.remove();
    var minutes = Math.round(gapSec / 60);
    var label;
    if (reason === 'tab_hidden')      label = 'tab was hidden';
    else if (reason === 'tick_gap')   label = 'tab was suspended (laptop sleep?)';
    else                              label = 'you were idle';
    var notice = document.createElement('div');
    notice.id = 'gap-notice';
    notice.setAttribute('role', 'status');
    notice.innerHTML = ''
      + '<style>'
      + '#gap-notice {'
      + '  position: fixed; top: 12px; left: 50%; transform: translateX(-50%);'
      + '  z-index: 4500; background: #2d2a26; color: #e8e1d0;'
      + '  border: 1px solid #c9b88a; border-radius: 6px;'
      + '  padding: 9px 14px; font-size: 13px;'
      + '  font-family: -apple-system, system-ui, sans-serif;'
      + '  display: flex; align-items: center; gap: 12px;'
      + '  box-shadow: 0 4px 16px rgba(0,0,0,0.45);'
      + '}'
      + '#gap-notice button {'
      + '  background: #c9b88a; color: #1a1814; border: none;'
      + '  padding: 4px 10px; border-radius: 3px; font-size: 12px;'
      + '  font-weight: 600; cursor: pointer;'
      + '}'
      + '#gap-notice button.ghost { background: transparent; color: #c9b88a; border: 1px solid #6a5a3a; }'
      + '#gap-notice button:hover { background: #ebd9a7; }'
      + '#gap-notice button.ghost:hover { background: #6a5a3a; color: #e8e1d0; }'
      + '</style>'
      + '<span>Welcome back. Clock was auto-paused for ' + minutes + ' min while ' + label + '. </span>'
      + '<button type="button" data-gap-count>Count it (+' + minutes + ' min)</button>'
      + '<button type="button" class="ghost" data-gap-dismiss>Skip</button>';
    document.body.appendChild(notice);
    notice.querySelector('[data-gap-count]').addEventListener('click', function() {
        clock.countTheGap(gapSec);
        notice.remove();
    });
    notice.querySelector('[data-gap-dismiss]').addEventListener('click', function() {
        notice.remove();
    });
    window.setTimeout(function() { if (notice.parentNode) notice.remove(); }, 30000);
}

/* ── Wellbeing Nudge ────────────────────────────────────────────────────── */

function showWellbeingNudge(text) {
    var el = document.getElementById('wellbeing-nudge');
    if (!el) return;
    var msgEl = el.querySelector('.nudge-text');
    if (msgEl) msgEl.textContent = text;
    el.classList.remove('hidden');
}

function dismissWellbeingNudge() {
    var el = document.getElementById('wellbeing-nudge');
    if (el) el.classList.add('hidden');
}

/* ── Card Functions ─────────────────────────────────────────────────────── */

function switchTab(btn) {
    var tabIndex = btn.getAttribute('data-tab');
    var container = btn.closest('.study-bible-container');
    if (!container) return;

    // Toggle tab active state
    var allTabs = container.querySelectorAll('.sb-tab');
    for (var i = 0; i < allTabs.length; i++) {
        allTabs[i].classList.remove('active');
    }
    btn.classList.add('active');

    // Toggle panel visibility
    var allPanels = container.querySelectorAll('.sb-panel');
    for (var j = 0; j < allPanels.length; j++) {
        if (allPanels[j].getAttribute('data-tab') === tabIndex) {
            allPanels[j].classList.add('active');
        } else {
            allPanels[j].classList.remove('active');
        }
    }
}

function copyCardResponse() {
    var textarea = document.getElementById('card-response');
    var hidden = document.getElementById('hidden-response');
    if (textarea && hidden) {
        hidden.value = textarea.value;
    }
}

/* ── Card Auto-Save ────────────────────────────────────────────────────── */

var _cardSaveTimer = null;

function setupCardAutosave() {
    var textarea = document.getElementById('card-response');
    if (!textarea || !window.studySessionId) return;
    textarea.addEventListener('input', function() {
        clearTimeout(_cardSaveTimer);
        _cardSaveTimer = setTimeout(function() {
            fetch('/study/session/' + window.studySessionId + '/card/autosave', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({content: textarea.value})
            });
        }, 1500);
    });
}

/* ── Star Annotations ───────────────────────────────────────────────────── */

var starPopup = null;

function setupStarAnnotations() {
    var sbTextElements = document.querySelectorAll('.sb-text');
    if (sbTextElements.length === 0) return;

    document.addEventListener('mouseup', function(e) {
        var selection = window.getSelection();
        var text = selection ? selection.toString().trim() : '';

        // Remove existing popup
        if (starPopup) {
            starPopup.remove();
            starPopup = null;
        }

        if (!text || text.length < 3) return;

        // Check if selection is inside a .sb-text element
        var anchor = selection.anchorNode;
        var insideSbText = false;
        var sourceAbbrev = '';
        var node = anchor;
        while (node) {
            if (node.nodeType === 1 && node.classList && node.classList.contains('sb-text')) {
                insideSbText = true;
                sourceAbbrev = node.getAttribute('data-source') || '';
                break;
            }
            node = node.parentNode;
        }
        if (!insideSbText) return;

        // Show star popup near selection
        var range = selection.getRangeAt(0);
        var rect = range.getBoundingClientRect();

        starPopup = document.createElement('div');
        starPopup.id = 'star-popup';
        starPopup.textContent = '\u2605';
        starPopup.title = 'Star this selection';
        starPopup.style.left = (rect.left + rect.width / 2 - 16) + 'px';
        starPopup.style.top = (rect.top - 36) + 'px';

        var selectedText = text;
        var source = sourceAbbrev;

        starPopup.addEventListener('mousedown', function(ev) {
            ev.preventDefault();
            ev.stopPropagation();

            var note = prompt('Add a note (optional):') || '';

            fetch('/study/session/' + window.studySessionId + '/annotate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    source: source,
                    starred_text: selectedText,
                    note: note
                })
            }).then(function(r) { return r.json(); }).then(function() {
                appendStarItem(source, selectedText, note);
                if (window.getSelection) window.getSelection().removeAllRanges();
            }).catch(function() {});

            if (starPopup) {
                starPopup.remove();
                starPopup = null;
            }
        });

        document.body.appendChild(starPopup);
    });

    // Remove popup on click elsewhere
    document.addEventListener('mousedown', function(e) {
        if (starPopup && e.target !== starPopup) {
            starPopup.remove();
            starPopup = null;
        }
    });
}

function appendStarItem(source, starredText, note) {
    var area = document.querySelector('.sb-notepad-area');
    if (!area) return;

    var item = document.createElement('div');
    item.className = 'sb-star-item';

    var icon = document.createElement('span');
    icon.className = 'star-icon';
    icon.textContent = '\u2605';
    item.appendChild(icon);

    var src = document.createElement('span');
    src.className = 'star-source';
    src.textContent = source + ':';
    item.appendChild(src);

    var txt = document.createElement('span');
    txt.className = 'star-text';
    txt.textContent = '"' + starredText + '"';
    item.appendChild(txt);

    if (note) {
        var noteEl = document.createElement('span');
        noteEl.className = 'star-note';
        noteEl.textContent = '\u2192 ' + note;
        item.appendChild(noteEl);
    }

    area.appendChild(item);
}

/* ── Notepad Auto-save ──────────────────────────────────────────────────── */

var notepadTimer = null;
function debouncedSaveNotepad(content) {
    clearTimeout(notepadTimer);
    notepadTimer = setTimeout(function() {
        fetch('/study/session/' + window.studySessionId + '/notepad', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ phase: 'study_bibles', content: content })
        }).catch(function() {});
    }, 1000);
}

/* ── SSE Streaming ──────────────────────────────────────────────────────── */

var isStreaming = false;

async function sendStudyMessage(sessionId, message) {
    if (!message.trim() || isStreaming) return;
    isStreaming = true;

    var inputEl = document.getElementById('study-input');
    var sendBtn = document.getElementById('btn-study-send');
    if (inputEl) { inputEl.disabled = true; inputEl.value = ''; }
    if (sendBtn) sendBtn.disabled = true;

    appendConvMessage('user', message);

    var assistantEl = appendConvMessage('assistant', '');

    var buffer = '';

    try {
        var response = await fetch('/study/session/' + sessionId + '/discuss', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: message })
        });

        if (!response.ok) {
            var bubble = assistantEl.querySelector('.msg-content');
            var info = null;
            try { info = await response.json(); } catch (e) {}
            if (response.status === 423 && info && info.message) {
                // Cutoff engaged (e.g. Friday 3pm / weekend lockout). Show the
                // real reason in plain English instead of "[Error 423]", and
                // flip the page into read-only mode the way the banner does.
                if (bubble) {
                    bubble.textContent = info.message + (
                        info.reopens_at ? ' (writing reopens ' + info.reopens_at + ')' : '');
                }
                document.body.classList.add('cutoff-enforced');
            } else if (bubble) {
                bubble.textContent = '[Error ' + response.status + (
                    info && info.message ? ': ' + info.message : '') + ']';
            }
            return;
        }

        var reader = response.body.getReader();
        var decoder = new TextDecoder();

        while (true) {
            var chunk = await reader.read();
            if (chunk.done) break;

            buffer += decoder.decode(chunk.value, { stream: true });

            var lines = buffer.split('\n');
            buffer = lines.pop();

            for (var i = 0; i < lines.length; i++) {
                var line = lines[i];
                if (!line.startsWith('data: ')) continue;
                var payload = line.slice(6).trim();
                if (!payload || payload === '[DONE]') continue;

                var data;
                try { data = JSON.parse(payload); } catch (e) { continue; }

                handleSSEEvent(data, assistantEl);
            }
        }
    } catch (err) {
        var errEl = assistantEl ? assistantEl.querySelector('.msg-content') : null;
        if (errEl) errEl.textContent = '[Connection error: ' + err.message + ']';
    } finally {
        isStreaming = false;
        if (inputEl) { inputEl.disabled = false; inputEl.focus(); }
        if (sendBtn) sendBtn.disabled = false;
        scrollConversation();
        studyClock.syncToServer();
    }
}

function handleSSEEvent(data, assistantEl) {
    if (data.type === 'text_delta') {
        var msgContent = assistantEl.querySelector('.msg-content');
        if (msgContent) msgContent.textContent += data.text;
        scrollConversation();

    } else if (data.type === 'tool_start') {
        appendToolStart(data.name);
        scrollConversation();

    } else if (data.type === 'tool_result') {
        renderToolResult(data.name, data.result);
        scrollConversation();

    } else if (data.type === 'outline_update') {
        refreshOutline();

    } else if (data.type === 'error') {
        var errContent = assistantEl.querySelector('.msg-content');
        if (errContent) errContent.textContent += '\n[Error: ' + (data.message || 'unknown') + ']';
    }
}

/* ── Message Rendering ──────────────────────────────────────────────────── */

function appendConvMessage(role, text) {
    var thread = document.getElementById('conversation-thread');
    if (!thread) return null;

    var wrapper = document.createElement('div');
    wrapper.className = 'conv-message ' + role;

    var content = document.createElement('div');
    content.className = 'msg-content';
    content.textContent = text;

    wrapper.appendChild(content);
    thread.appendChild(wrapper);
    scrollConversation();
    return wrapper;
}

function getSourcesList() {
    var list = document.getElementById('sources-list');
    if (!list) return null;
    // First tool call clears the empty-state hint.
    var empty = list.querySelector('.sources-empty');
    if (empty) empty.remove();
    return list;
}

function appendToolStart(name) {
    var list = getSourcesList();
    if (!list) return;

    // Reuse a pending placeholder for this tool if one already exists
    // (de-dupes rapid "Searching: grammars\u2026 / grammars\u2026 / grammars\u2026" runs).
    var pending = list.querySelector('.source-card.pending[data-tool="' + name + '"]');
    if (pending) {
        list.appendChild(pending);  // bump to bottom = most recent
        return;
    }

    var el = document.createElement('div');
    el.className = 'source-card pending';
    el.dataset.tool = name;
    el.innerHTML = '<span class="source-spinner" aria-hidden="true"></span>' +
                   '<span class="source-label">Searching ' + formatToolName(name) + '\u2026</span>';
    list.appendChild(el);
    list.scrollTop = list.scrollHeight;
}

function formatToolName(name) {
    var names = {
        'read_bible_passage': 'Bible',
        'find_commentary_paragraph': 'commentaries',
        'lookup_lexicon': 'lexicons',
        'lookup_grammar': 'grammars',
        'word_study_lookup': 'interlinear',
        'expand_cross_references': 'cross-references',
        'save_to_outline': 'saving to outline',
        'get_passage_data': 'Logos datasets',
        'get_cross_reference_network': 'cross-reference network',
        'get_passage_context': 'passage context'
    };
    return names[name] || name.replace(/_/g, ' ');
}

/* ── Rich Content Block Rendering ───────────────────────────────────────── */

function renderToolResult(name, result) {
    // save_to_outline is a write-side notification, not a research source.
    // Keep it inline in the conversation thread as an "insight" pill so
    // Bryan sees the confirmation next to the message that triggered it.
    if (name === 'save_to_outline') {
        var thread = document.getElementById('conversation-thread');
        if (!thread) return;
        var pillText = '';
        if (typeof result === 'object' && result !== null) {
            pillText = result.content || result.message || JSON.stringify(result);
        } else {
            pillText = String(result);
        }
        var pill = document.createElement('div');
        pill.className = 'insight-pill';
        var pillSpan = document.createElement('span');
        pillSpan.textContent = 'Saved: ' + pillText;
        pill.appendChild(pillSpan);
        thread.appendChild(pill);
        refreshOutline();
        return;
    }

    // All other tool calls (Bible / lexicon / grammar / commentary /
    // cross-refs / search) flow into the Sources panel on the left so
    // they don't push the chat upward as the AI streams.
    var list = getSourcesList();
    if (!list) return;

    var pending = list.querySelector('.source-card.pending[data-tool="' + name + '"]');
    var card = pending || document.createElement('div');
    card.className = 'source-card';
    card.dataset.tool = name;
    while (card.firstChild) card.removeChild(card.firstChild);

    var details = document.createElement('details');
    var summary = document.createElement('summary');
    summary.textContent = formatToolName(name);
    details.appendChild(summary);

    var body = document.createElement('div');
    body.className = 'tool-body';
    if (typeof result === 'string') {
        body.textContent = result;
    } else {
        try { body.textContent = JSON.stringify(result, null, 2); }
        catch (e) { body.textContent = String(result); }
    }
    details.appendChild(body);
    card.appendChild(details);

    if (!pending) list.appendChild(card);
    list.scrollTop = list.scrollHeight;
}

/* ── Scroll ─────────────────────────────────────────────────────────────── */

function scrollConversation() {
    var thread = document.getElementById('conversation-thread');
    if (thread) thread.scrollTop = thread.scrollHeight;
}

/* ── Outline Sidebar ────────────────────────────────────────────────────── */

function refreshOutline() {
    if (!window.studySessionId) return;
    fetch('/study/session/' + window.studySessionId + '/outline')
        .then(function(r) { return r.json(); })
        .then(function(tree) { renderOutlineTree(tree); })
        .catch(function() {});
}

function renderOutlineTree(tree) {
    var body = document.getElementById('outline-tree-container');
    if (!body) return;

    while (body.firstChild) body.removeChild(body.firstChild);

    if (!tree || tree.length === 0) {
        var empty = document.createElement('p');
        empty.style.cssText = 'color:var(--text-dim);font-size:13px;font-style:italic;padding:8px 0;';
        empty.textContent = 'Outline builds as you study.';
        body.appendChild(empty);
        return;
    }

    var ul = buildOutlineUL(tree);
    body.appendChild(ul);
}

function buildOutlineUL(nodes) {
    var ul = document.createElement('ul');
    ul.className = 'outline-tree';

    for (var i = 0; i < nodes.length; i++) {
        var node = nodes[i];
        var li = document.createElement('li');
        li.className = 'outline-node';
        if (node.type === 'main_point' || node.type === 'title') li.classList.add('is-main');
        li.dataset.id = String(node.id);

        var row = document.createElement('div');
        row.className = 'outline-node-row';

        var textSpan = document.createElement('span');
        textSpan.className = 'outline-node-text';
        textSpan.textContent = node.content;

        row.appendChild(textSpan);

        if (node.verse_ref) {
            var ref = document.createElement('span');
            ref.className = 'outline-node-ref';
            ref.textContent = node.verse_ref;
            row.appendChild(ref);
        }

        // Actions
        var actions = document.createElement('div');
        actions.className = 'outline-node-actions';

        var editBtn = document.createElement('button');
        editBtn.className = 'outline-btn';
        editBtn.textContent = 'Edit';
        editBtn.title = 'Edit';
        (function(nid, textEl) {
            editBtn.addEventListener('click', function() {
                editOutlineNode(nid, textEl);
            });
        })(node.id, textSpan);

        var delBtn = document.createElement('button');
        delBtn.className = 'outline-btn del';
        delBtn.textContent = 'Del';
        delBtn.title = 'Delete';
        (function(nid) {
            delBtn.addEventListener('click', function() {
                deleteOutlineNode(nid);
            });
        })(node.id);

        actions.appendChild(editBtn);
        actions.appendChild(delBtn);
        row.appendChild(actions);

        li.appendChild(row);

        if (node.children && node.children.length > 0) {
            li.appendChild(buildOutlineUL(node.children));
        }

        ul.appendChild(li);
    }
    return ul;
}

function editOutlineNode(nodeId, textEl) {
    if (!textEl) return;
    var original = textEl.textContent;

    var input = document.createElement('input');
    input.type = 'text';
    input.value = original;
    input.style.cssText = 'background:var(--bg-input);border:1px solid var(--border-focus);border-radius:3px;color:var(--text);font-size:13px;padding:1px 6px;width:100%;outline:none;';

    textEl.replaceWith(input);
    input.focus();
    input.select();

    function save() {
        var newText = input.value.trim() || original;
        var span = document.createElement('span');
        span.className = 'outline-node-text';
        span.textContent = newText;
        input.replaceWith(span);

        if (newText !== original && window.studySessionId) {
            fetch('/study/session/' + window.studySessionId + '/outline/' + nodeId, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ content: newText })
            }).catch(function() {});
        }
    }

    input.addEventListener('blur', save);
    input.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
        if (e.key === 'Escape') { input.value = original; input.blur(); }
    });
}

function deleteOutlineNode(nodeId) {
    if (!window.studySessionId) return;
    fetch('/study/session/' + window.studySessionId + '/outline/' + nodeId, {
        method: 'DELETE'
    }).then(function(r) {
        if (r.ok) refreshOutline();
    }).catch(function() {});
}

function addOutlineNote(text, nodeType) {
    if (!window.studySessionId || !text.trim()) return;
    fetch('/study/session/' + window.studySessionId + '/outline/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: text, node_type: nodeType || 'note' })
    }).then(function(r) {
        if (r.ok) refreshOutline();
    }).catch(function() {});
}

/* ── Word Popup (click Greek/Hebrew word for parsing) ──────────────────── */

var _wordPopup = null;
var _wordCache = {};

function setupWordPopups() {
    var textEl = document.getElementById('clickable-text');
    if (!textEl) return;

    // Wrap each Greek/Hebrew word in a clickable span using DOM methods
    var walker = document.createTreeWalker(textEl, NodeFilter.SHOW_TEXT, null, false);
    var textNodes = [];
    var node;
    while ((node = walker.nextNode())) textNodes.push(node);

    var greekOrHebrew = /[\u0370-\u03FF\u1F00-\u1FFF\u0590-\u05FF\uFB1D-\uFB4F][\u0300-\u036F\u0370-\u03FF\u1F00-\u1FFF\u0590-\u05FF]*/;
    textNodes.forEach(function(tn) {
        var text = tn.textContent;
        if (!greekOrHebrew.test(text)) return;
        var frag = document.createDocumentFragment();
        // Split into words and non-words
        var parts = text.split(/([\u0370-\u03FF\u1F00-\u1FFF\u0590-\u05FF\uFB1D-\uFB4F][\u0300-\u036F\u0370-\u03FF\u1F00-\u1FFF\u0590-\u05FF\uFB1D-\uFB4F]*)/);
        parts.forEach(function(part) {
            if (greekOrHebrew.test(part)) {
                var span = document.createElement('span');
                span.className = 'clickable-word';
                span.textContent = part;
                frag.appendChild(span);
            } else {
                frag.appendChild(document.createTextNode(part));
            }
        });
        tn.parentNode.replaceChild(frag, tn);
    });

    // Add click handler
    textEl.addEventListener('click', function(e) {
        var wordEl = e.target.closest('.clickable-word');
        if (!wordEl) {
            dismissWordPopup();
            return;
        }
        var word = wordEl.textContent.trim();
        if (word) showWordInfo(word, wordEl);
    });

    // Dismiss on click outside
    document.addEventListener('click', function(e) {
        if (_wordPopup && !e.target.closest('.clickable-word') && !e.target.closest('.word-popup')) {
            dismissWordPopup();
        }
    });
}

function showWordInfo(word, anchorEl) {
    dismissWordPopup();

    // Check cache first
    if (_wordCache[word]) {
        renderWordPopup(_wordCache[word], anchorEl);
        return;
    }

    // Show loading state
    renderWordPopup({lemma: '...', gloss: 'loading...', parsing: '', root: ''}, anchorEl);

    fetch('/study/session/' + window.studySessionId + '/word-info', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({word: word})
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.error) {
            renderWordPopup({lemma: word, gloss: 'lookup failed', parsing: '', root: ''}, anchorEl);
        } else {
            _wordCache[word] = data;
            renderWordPopup(data, anchorEl);
        }
    })
    .catch(function() {
        renderWordPopup({lemma: word, gloss: 'error', parsing: '', root: ''}, anchorEl);
    });
}

function renderWordPopup(info, anchorEl) {
    dismissWordPopup();
    var popup = document.createElement('div');
    popup.className = 'word-popup';

    var lemmaDiv = document.createElement('div');
    lemmaDiv.className = 'wp-lemma';
    lemmaDiv.textContent = info.lemma || '';
    popup.appendChild(lemmaDiv);

    var glossDiv = document.createElement('div');
    glossDiv.className = 'wp-gloss';
    glossDiv.textContent = info.gloss || '';
    popup.appendChild(glossDiv);

    var parseDiv = document.createElement('div');
    parseDiv.className = 'wp-parsing';
    parseDiv.textContent = info.parsing || '';
    popup.appendChild(parseDiv);

    if (info.root && info.root !== info.lemma) {
        var rootDiv = document.createElement('div');
        rootDiv.className = 'wp-root';
        rootDiv.textContent = 'Root: ' + info.root;
        popup.appendChild(rootDiv);
    }

    // Position below the clicked word
    document.body.appendChild(popup);
    var rect = anchorEl.getBoundingClientRect();
    popup.style.left = Math.max(8, rect.left) + 'px';
    popup.style.top = (rect.bottom + window.scrollY + 6) + 'px';
    _wordPopup = popup;
}

function dismissWordPopup() {
    if (_wordPopup) {
        _wordPopup.remove();
        _wordPopup = null;
    }
}

/* ── DOMContentLoaded ───────────────────────────────────────────────────── */

document.addEventListener('DOMContentLoaded', function() {

    // Conversation mode: wire input and send button
    if (window.studyMode === 'conversation') {
        var inputEl = document.getElementById('study-input');
        if (inputEl) {
            inputEl.addEventListener('keydown', function(e) {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    var msg = inputEl.value.trim();
                    if (msg && window.studySessionId) {
                        sendStudyMessage(window.studySessionId, msg);
                    }
                }
            });

            // Auto-resize. When content exceeds max-height, keep the cursor
            // visible — otherwise the style.height reset causes the textarea's
            // internal scroll to jump to the top, hiding what you're typing.
            inputEl.addEventListener('input', function() {
                inputEl.style.height = 'auto';
                var contentHeight = inputEl.scrollHeight;
                inputEl.style.height = Math.min(contentHeight, 160) + 'px';
                if (contentHeight > 160) {
                    inputEl.scrollTop = inputEl.scrollHeight;
                }
            });
        }

        var sendBtn = document.getElementById('btn-study-send');
        if (sendBtn) {
            sendBtn.addEventListener('click', function() {
                var msg = inputEl ? inputEl.value.trim() : '';
                if (msg && window.studySessionId) {
                    sendStudyMessage(window.studySessionId, msg);
                }
            });
        }

        // Scroll conversation to bottom
        scrollConversation();
    }

    // Card mode: set up star annotations, word popups, and auto-save
    if (window.studyMode === 'card') {
        setupStarAnnotations();
        setupWordPopups();
        setupCardAutosave();
    }

    // Dismiss wellbeing nudge
    var nudgeDismiss = document.getElementById('btn-dismiss-nudge');
    if (nudgeDismiss) {
        nudgeDismiss.addEventListener('click', dismissWellbeingNudge);
    }

    // Outline add-note input
    var outlineInput = document.getElementById('outline-add-input');
    var outlineTypeSelect = document.getElementById('outline-add-type');
    if (outlineInput) {
        outlineInput.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                var text = outlineInput.value.trim();
                var ntype = outlineTypeSelect ? outlineTypeSelect.value : 'note';
                if (text) {
                    addOutlineNote(text, ntype);
                    outlineInput.value = '';
                }
            }
        });
    }

    // Load outline on page load
    refreshOutline();
    refreshHorizon();

    // Sync clock + refresh the week-horizon countdown every 60 seconds. The
    // "to Friday 3pm" label is otherwise server-rendered once and freezes
    // until a page reload (bug 2026-05-22).
    setInterval(function() { studyClock.syncToServer(); refreshHorizon(); }, 60000);
});

/* Refresh the week-horizon countdown label + bar from the server so the
 * "to Friday 3pm" text and the green→amber→red fill stay live without a
 * page reload. Minute granularity, so the 60s timer is plenty. */
function refreshHorizon() {
    if (!window.studySessionId) return;
    fetch('/study/session/' + window.studySessionId + '/horizon')
        .then(function(r) { return r.ok ? r.json() : null; })
        .then(function(h) {
            if (!h) return;
            var tip = document.querySelector('.horizon-tooltip');
            if (tip) {
                tip.textContent = h.label || '';
                tip.className = 'horizon-tooltip ' + (h.color || '');
            }
            var fill = document.querySelector('.horizon-bar .horizon-fill');
            if (fill) {
                fill.className = 'horizon-fill ' + (h.color || '');
                if (h.state === 'in_window' && typeof h.fraction === 'number') {
                    var pct = Math.round(h.fraction * 1000) / 10;
                    fill.style.width = pct + '%';
                    var marker = document.querySelector('.horizon-marker');
                    if (marker) marker.style.left = 'calc(' + pct + '% - 1px)';
                } else {
                    fill.style.width = '';  // CSS drives full-width lockout/muted states
                }
            }
        })
        .catch(function() {});
}

/* ── Exports ────────────────────────────────────────────────────────────── */

Object.assign(window, {
    studyClock: studyClock,
    sendStudyMessage: sendStudyMessage,
    appendConvMessage: appendConvMessage,
    scrollConversation: scrollConversation,
    refreshOutline: refreshOutline,
    showWellbeingNudge: showWellbeingNudge,
    dismissWellbeingNudge: dismissWellbeingNudge,
    addOutlineNote: addOutlineNote,
    switchTab: switchTab,
    copyCardResponse: copyCardResponse,
    debouncedSaveNotepad: debouncedSaveNotepad
});

/* ── Split-into-series modal ────────────────────────────────────────────── */
(function () {
  const openBtn = document.getElementById('open-split-modal');
  if (!openBtn) return;
  const modal = document.getElementById('series-split-modal');
  const tbody = modal.querySelector('#series-split-rows tbody');
  const errBox = modal.querySelector('#series-split-error');
  const loading = modal.querySelector('#series-split-loading');
  const addBtn = modal.querySelector('#series-split-add');
  const confirmBtn = modal.querySelector('#series-split-confirm');
  const sessionId = openBtn.dataset.sessionId;

  function renderRow(row) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><input type="number" min="1" value="${row.position}" class="pos" style="width:3em"></td>
      <td><input type="text" value="${(row.title || '').replace(/"/g, '&quot;')}" class="title"></td>
      <td>
        <input type="number" min="1" value="${row.verse_start}" class="vs" style="width:4em"> –
        <input type="number" min="1" value="${row.verse_end}" class="ve" style="width:4em">
      </td>
      <td><button type="button" class="del">×</button></td>`;
    tr.querySelector('.del').onclick = () => tr.remove();
    return tr;
  }

  function readRows() {
    return Array.from(tbody.querySelectorAll('tr')).map(tr => ({
      position: parseInt(tr.querySelector('.pos').value, 10),
      title: tr.querySelector('.title').value.trim(),
      verse_start: parseInt(tr.querySelector('.vs').value, 10),
      verse_end: parseInt(tr.querySelector('.ve').value, 10),
    }));
  }

  openBtn.addEventListener('click', async () => {
    tbody.innerHTML = '';
    errBox.hidden = true;
    loading.hidden = false;
    modal.showModal();
    try {
      const resp = await fetch(`/study/session/${sessionId}/split/propose`, {method: 'POST'});
      const body = await resp.json();
      (body.plan || []).forEach(r => tbody.appendChild(renderRow(r)));
      if (!body.plan || body.plan.length === 0) {
        // Seed with one empty row
        tbody.appendChild(renderRow({position: 1, title: '', verse_start: 1, verse_end: 1}));
      }
    } catch (e) {
      errBox.textContent = 'Could not load proposal: ' + e.message;
      errBox.hidden = false;
      tbody.appendChild(renderRow({position: 1, title: '', verse_start: 1, verse_end: 1}));
    } finally {
      loading.hidden = true;
    }
  });

  addBtn.addEventListener('click', () => {
    const next = tbody.querySelectorAll('tr').length + 1;
    tbody.appendChild(renderRow({position: next, title: '', verse_start: 1, verse_end: 1}));
  });

  confirmBtn.addEventListener('click', async (ev) => {
    ev.preventDefault();
    errBox.hidden = true;
    const plan = readRows();
    const resp = await fetch(`/study/session/${sessionId}/split`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({plan}),
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({error: 'unknown'}));
      errBox.textContent = 'Split failed: ' + body.error;
      errBox.hidden = false;
      return;
    }
    modal.close();
    window.location.reload();
  });
})();

// Sermon-pack source-breakdown pill
// Fetches the latest per-artifact metrics for this session and renders
// a one-line summary in the sidebar pill. Quiet by design: when no
// generations have happened yet, the pill shows "not yet built".
(function () {
    const pill = document.getElementById('sermon-pack-pill');
    if (!pill || !window.studySessionId) return;
    const valueEl = pill.querySelector('.pill-value');

    async function refresh() {
        try {
            const r = await fetch('/study/session/' + window.studySessionId + '/pack/metrics');
            if (!r.ok) return;
            const data = await r.json();
            if (!data || Object.keys(data).length === 0) {
                valueEl.textContent = 'not yet built';
                valueEl.setAttribute('data-empty', '');
                return;
            }
            const parts = [];
            for (const art of ['manuscript', 'outline', 'handout']) {
                if (!data[art]) continue;
                const pct = Math.round(data[art].mining_percentage * 100);
                parts.push(art[0].toUpperCase() + ': ' + pct + '%');
            }
            valueEl.textContent = parts.join('  ');
            valueEl.removeAttribute('data-empty');
            // detailed breakdown in the title for hover-tooltip
            const lines = [];
            for (const art of ['manuscript', 'outline', 'handout']) {
                if (!data[art]) continue;
                const br = data[art].source_breakdown;
                const summary = Object.entries(br)
                    .filter(function (e) { return e[1] > 0; })
                    .map(function (e) { return e[0] + ':' + e[1]; })
                    .join(', ');
                lines.push(art + ' (' + summary + ')');
            }
            pill.title = 'Last build source breakdown\n' + lines.join('\n');
        } catch (e) {
            // quiet: pill stays at default text
        }
    }

    refresh();
    document.addEventListener('sermon-pack-generated', refresh);
})();
