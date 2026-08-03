// C:\PCHost\web\universal_audio_handler.js
/**
 * Universal Audio Handler for DreamCision
 * Handles audio recording and speech recognition across all mobile browsers
 * 
 * Usage: Include this file in your HTML and call initUniversalAudio()
 */

class UniversalAudioHandler {
    constructor() {
        this.capabilities = this.detectBrowserCapabilities();
        this.mediaRecorder = null;
        this.audioChunks = [];
        this.isRecording = false;
        this.speechRecognition = null;
        this.isListening = false;
        this.shouldKeepListening = false; // keep mic alive unless user stops
        this.shouldKeepRecording = false; // keep recording unless user stops
        this._recordingGeneration = 0;    // generation counter — incremented on stop to abort stale starts
        this.onTranscriptionCallback = null;
        this.onAudioFileCallback = null;
        this.wakeLock = null; // screen wake lock handle
        
        this.onAsrStatusCallback = null;
        this.onInputSilenceCallback = null; // (boolean sustainedSilent) — sustained ~digital silence on mic input
        this.liveStream = null;
        this._recordingSession = null;
        this._chunkPipeline = null;
        this._segmentRotateTimer = null;
        this._segmentRotatePending = false;
        this._sessionBackupChunks = [];

        this._inputAudioCtx = null;
        this._inputAnalyser = null;
        this._inputLevelRaf = null;
        this._inputSilentAccumMs = 0;
        this._inputLastTickMs = 0;
        this._inputSilenceAlerted = false;

        // Debug: enable with `window.__ASR_DEBUG = true` or localStorage.setItem('asr_debug','1')
        this._asrDebugStartTs = 0;
    }

    _asrDebugEnabled() {
        try {
            if (window && window.__ASR_DEBUG) return true;
        } catch (_) {}
        try {
            const v = localStorage.getItem('asr_debug');
            if (v && String(v).trim() && String(v).trim() !== '0') return true;
        } catch (_) {}
        return false;
    }

    _asrDbg(msg, extra = {}) {
        if (!this._asrDebugEnabled()) return;
        try {
            const base = {
                t_ms: this._asrDebugStartTs ? (Date.now() - this._asrDebugStartTs) : null,
                isRecording: !!this.isRecording,
                isListening: !!this.isListening,
            };
            console.info('[ASR_DEBUG]', msg, Object.assign(base, extra || {}));
        } catch (_) {}
    }

    _reportAsrIncident(payload) {
        // Best-effort: persist a structured client incident on the FastAPI host so operators/agents
        // can read `Clinical-Note-Generator/asr_diagnostics/last_incident.json` without copy/paste.
        try {
            const p = payload && typeof payload === 'object' ? payload : {};
            let token = null;
            if (typeof window.getAuthToken === 'function') token = window.getAuthToken();
            if (!token) token = window.app?.settings?.apiKey || '';
            if (!token) {
                try { token = sessionStorage.getItem('auth_access_token') || ''; } catch (_) {}
            }
            if (!token) return;
            const apiBase = (typeof window.getApiBase === 'function' && window.app && window.app.settings)
                ? window.getApiBase(window.app.settings.serverUrl || '/api')
                : '/api';
            const url = `${apiBase}/asr_client_incident`;
            const body = JSON.stringify(Object.assign({}, p, {
                user_agent: (navigator && navigator.userAgent) ? navigator.userAgent : '',
            }));
            // Prefer keepalive beacon-like behavior without blocking UI.
            if (typeof fetch === 'function') {
                fetch(url, {
                    method: 'POST',
                    headers: {
                        'Authorization': `Bearer ${token}`,
                        'Content-Type': 'application/json',
                    },
                    body,
                    keepalive: true,
                }).catch(() => {});
            }
        } catch (_) {}
    }


    setAsrStatusCallback(callback) {
        this.onAsrStatusCallback = callback;
    }

    _asrStatus(state, detail = '') {
        if (this.onAsrStatusCallback) this.onAsrStatusCallback(state, detail);
    }

    // Detect browser and platform capabilities
    detectBrowserCapabilities() {
        const userAgent = navigator.userAgent;
        const capabilities = {
            // Platform detection
            isAndroid: /Android/.test(userAgent),
            isIOS: /iPhone|iPad|iPod/.test(userAgent),
            
            // Browser detection
            isChrome: /Chrome/.test(userAgent) && !/Edg/.test(userAgent) && !/CriOS/.test(userAgent),
            isFirefox: /Firefox/.test(userAgent) && !/FxiOS/.test(userAgent),
            isSafari: /Safari/.test(userAgent) && !/Chrome/.test(userAgent) && !/CriOS/.test(userAgent) && !/FxiOS/.test(userAgent),
            isChromeiOS: /CriOS/.test(userAgent) || (/Chrome/.test(userAgent) && /iPhone|iPad|iPod/.test(userAgent)),
            isFirefoxiOS: /FxiOS/.test(userAgent),
            isEdge: /Edg/.test(userAgent),
            isSamsung: /SamsungBrowser/.test(userAgent),
            
            // Feature detection
            hasSpeechRecognition: !!(window.SpeechRecognition || window.webkitSpeechRecognition),
            hasGetUserMedia: !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia),
            hasMediaRecorder: !!window.MediaRecorder,
            
            // Capability flags
            canUseSpeechRecognition: false,
            canUseAudioRecording: false,
            preferredAudioMethod: 'file'
        };
        
        // Determine speech recognition capabilities.
        // NOTE: On iOS Safari the actual window.SpeechRecognition API does not exist,
        // but canUseSpeechRecognition is intentionally set true because the recording
        // path (startSpeechRecognition -> startAudioRecording -> MediaRecorder) works via
        // getUserMedia + MediaRecorder, not the SpeechRecognition API. This flag
        // name is misleading — it means "can capture speech audio", not "can use Web Speech API".
        if (capabilities.isAndroid) {
            capabilities.canUseSpeechRecognition = capabilities.isChrome || capabilities.isFirefox || capabilities.isSamsung;
        } else if (capabilities.isIOS) {
            capabilities.canUseSpeechRecognition = capabilities.isSafari; // Audio capture works on Safari
        } else {
            capabilities.canUseSpeechRecognition = capabilities.hasSpeechRecognition;
        }
        
        // Determine audio recording capabilities
        capabilities.canUseAudioRecording = capabilities.hasGetUserMedia && capabilities.hasMediaRecorder;
        
        // Set preferred method
        if (capabilities.canUseSpeechRecognition) {
            capabilities.preferredAudioMethod = 'speech';
        } else if (capabilities.canUseAudioRecording) {
            capabilities.preferredAudioMethod = 'recording';
        } else {
            capabilities.preferredAudioMethod = 'file';
        }
        
        return capabilities;
    }

    // Legacy init hook — recording uses MediaRecorder only (no browser SpeechRecognition).
    initSpeechRecognition() {
        if (!this.capabilities.canUseAudioRecording) {
            return {
                available: false,
                reason: this.getRecordingUnavailableReason()
            };
        }
        return { available: true, method: 'media_recorder' };
    }

    // Initialize audio recording
    initAudioRecording() {
        if (!this.capabilities.canUseAudioRecording) {
            return {
                available: false,
                reason: this.getRecordingUnavailableReason()
            };
        }
        
        return { available: true, method: 'media_recorder' };
    }

    /**
     * Start a clinical recording session.
     *
     * IMPORTANT: Despite the name, this does NOT use the browser SpeechRecognition
     * API. It uses getUserMedia() + MediaRecorder to capture WebM/Opus audio, then calls
     * the ASR segment upload/transcribe callbacks on stop.
     *
     * New code should use the alias startAsrRecording().
     */
    async startSpeechRecognition() {
        console.log('[AudioHandler] Recording — transcription when you stop');
        this.isListening = true;
        this._asrStatus('recording', 'Recording — transcription runs when you stop');
        try {
            if (window.CNGAudioUI && typeof window.CNGAudioUI.syncGenerateButtonsForPipeline === 'function') {
                const ph = (window.app && window.app.audioPipelinePhase) || 'idle';
                window.CNGAudioUI.syncGenerateButtonsForPipeline(ph);
            }
        } catch (_) {}
        try {
            await this.startAudioRecording();
        } catch (e) {
            this.isListening = false;
            throw e;
        }
    }

    /**
     * Stop recording and finalizes the WebM backup + transcription callback.
     * Prefer the alias stopAsrRecording() in new code.
     */
    async stopSpeechRecognition() {
        this.isListening = false;
        this._asrStatus('processing', 'Finalizing recording…');
        this.stopAudioRecording();
        try {
            if (window.CNGAudioUI && typeof window.CNGAudioUI.syncGenerateButtonsForPipeline === 'function') {
                const ph = (window.app && window.app.audioPipelinePhase) || 'idle';
                window.CNGAudioUI.syncGenerateButtonsForPipeline(ph);
            }
        } catch (_) {}
    }

    // Correctly-named aliases for new call sites.
    async startAsrRecording() { return this.startSpeechRecognition(); }
    async stopAsrRecording() { return this.stopSpeechRecognition(); }

    // Start audio recording
    setChunkPipeline(pipeline) {
        this._chunkPipeline = pipeline || null;
    }

    _clearChunkSegmentRotation() {
        if (this._segmentRotateTimer) {
            clearTimeout(this._segmentRotateTimer);
            this._segmentRotateTimer = null;
        }
        this._segmentRotatePending = false;
    }

    _scheduleChunkSegmentRotation() {
        var self = this;
        this._clearChunkSegmentRotation();
        if (!this._chunkPipeline) return;
        var ms = (typeof window !== 'undefined' && window.ASR_CHUNK_SEGMENT_MS) ? window.ASR_CHUNK_SEGMENT_MS : 25000;
        this._segmentRotateTimer = setTimeout(function () {
            if (self.isRecording && self.mediaRecorder && self.mediaRecorder.state === 'recording') {
                self._segmentRotatePending = true;
                try { self.mediaRecorder.stop(); } catch (_) {}
            }
        }, ms);
    }

    _segmentDurationMs(state) {
        var base = state && (state.segmentStartedAt || state.startedAt);
        return base ? Math.max(0, Date.now() - base) : 0;
    }

    _shouldSubmitChunkBlob(blob, state, isFinal) {
        if (!blob || blob.size < 1000) return false;
        if (!isFinal) return true;
        var durMs = this._segmentDurationMs(state);
        if (durMs >= 1200) return true;
        return blob.size >= 12000;
    }

    _bindMediaRecorderHandlers(state, ctx) {
        var self = this;
        ctx = ctx || {};
        var recoveryId = ctx.recoveryId;
        var recoverySeqRef = ctx.recoverySeqRef;
        var localRecordingFileName = ctx.localRecordingFileName;

        this.mediaRecorder.ondataavailable = async function (event) {
            var blob = event && event.data;
            if (!blob || !blob.size) return;

            if (self.audioChunks.length === 0 && blob.size >= 4 &&
                blob.type && blob.type.indexOf('webm') !== -1) {
                blob.slice(0, 4).arrayBuffer().then(function (buf) {
                    var dv = new DataView(buf);
                    if (dv.getUint8(0) !== 0x1A || dv.getUint8(1) !== 0x45 ||
                        dv.getUint8(2) !== 0xDF || dv.getUint8(3) !== 0xA3) {
                        console.warn('[Audio] First chunk missing EBML header — recording may be corrupt');
                        self._reportAsrIncident({
                            stage: 'client.ebml_header_missing',
                            outcome: 'warning',
                            trace_id: '',
                            message: 'First MediaRecorder chunk missing EBML 1A45DFA3 header',
                        });
                    }
                }).catch(function () {});
            }

            self.audioChunks.push(blob);
            self._sessionBackupChunks.push(blob);
            self._asrDbg('backup.append', { size: blob.size, type: blob.type, totalChunks: self.audioChunks.length });
            if (recoveryId && window.RecordingRecovery && typeof window.RecordingRecovery.appendChunk === 'function') {
                var seq = recoverySeqRef.value++;
                var wrote = false;
                try {
                    wrote = await window.RecordingRecovery.appendChunk(recoveryId, seq, blob);
                } catch (e) {
                    wrote = false;
                }
                if (!wrote) {
                    console.warn('[Audio] recovery chunk write failed — crash recovery for this segment is unavailable', { seq: seq });
                    self._asrDbg('recovery.chunk_write_failed', { seq: seq });
                    self._reportAsrIncident({
                        stage: 'client.recovery_chunk_write_failed',
                        outcome: 'warning',
                        trace_id: '',
                        message: 'IndexedDB recovery chunk ' + seq + ' failed to persist; a crash now would lose this segment on recovery',
                    });
                }
            }
        };

        this.mediaRecorder.onerror = function () {
            state.hadErrors = true;
            self._clearChunkSegmentRotation();
            if (self._recordingTimeout) {
                clearTimeout(self._recordingTimeout);
                self._recordingTimeout = null;
            }
            self._recordingLock = false;
            self.isRecording = false;
            self.onRecordingError(new Error('Recorder error'));
            self._asrDbg('mediaRecorder.error', {});
        };

        this.mediaRecorder.onstop = function () {
            self._onMediaRecorderStop(state, ctx).catch(function (e) {
                console.warn('[Audio] onstop handler error', e);
            });
        };
    }

    async _onMediaRecorderStop(state, ctx) {
        ctx = ctx || {};
        var stream = state.stream;
        var recoveryId = ctx.recoveryId;
        var localRecordingFileName = ctx.localRecordingFileName;
        var options = ctx.recorderOptions || {};
        var gen = ctx.gen;

        if (this._recordingTimeout) {
            clearTimeout(this._recordingTimeout);
            this._recordingTimeout = null;
        }

        // ── 25s segment rotation (chunk pipeline, user still recording) ──
        var rotationChunkSubmitted = false;
        if (this._segmentRotatePending && this._chunkPipeline) {
            this._segmentRotatePending = false;
            var continueRecording = this.shouldKeepRecording && this._recordingGeneration === gen;
            try {
                var rotType = this.mediaRecorder.mimeType || this.audioChunks[0]?.type || 'audio/webm';
                var rotBlob = new Blob(this.audioChunks, { type: rotType });
                if (this._shouldSubmitChunkBlob(rotBlob, state, false)) {
                    this._asrStatus('processing', 'Saving segment for transcription…');
                    await this._chunkPipeline.submitSegment(rotBlob, {
                        isFinal: false,
                        stoppedAt: new Date().toISOString(),
                        durationSec: this._segmentDurationMs(state) / 1000,
                    });
                    rotationChunkSubmitted = true;
                }
                if (continueRecording) {
                    this.audioChunks = [];
                    state.segmentStartedAt = Date.now();
                    this.mediaRecorder = new MediaRecorder(stream, options);
                    this._bindMediaRecorderHandlers(state, ctx);
                    this.mediaRecorder.start(5000);
                    this.isRecording = true;
                    this._scheduleChunkSegmentRotation();
                    this._asrStatus('recording', 'Recording next segment…');
                    return;
                }
                // User stopped during rotation — clear partial recorder buffer and finalize below.
                this.audioChunks = [];
            } catch (e) {
                console.warn('[Audio] segment rotation failed', e);
                if (continueRecording) {
                    return;
                }
            }
        }

        this._clearChunkSegmentRotation();

        var transcribeOk = false;
        var uploadOk = false;
        try {
            var outType = this.mediaRecorder.mimeType || this.audioChunks[0]?.type || 'audio/webm';
            var backupSource = (this._chunkPipeline && this._sessionBackupChunks.length)
                ? this._sessionBackupChunks
                : this.audioChunks;
            var audioBlob = new Blob(backupSource, { type: outType });

            if (this._chunkPipeline) {
                var finalBlob = new Blob(this.audioChunks, { type: outType });
                if (!rotationChunkSubmitted && this._shouldSubmitChunkBlob(finalBlob, state, true)) {
                    this._asrStatus('processing', 'Saving final segment…');
                    await this._chunkPipeline.submitSegment(finalBlob, {
                        isFinal: true,
                        stoppedAt: new Date().toISOString(),
                        durationSec: this._segmentDurationMs(state) / 1000,
                    });
                }
                this._asrStatus('processing', 'Finishing transcription…');
                // 120s hard deadline on finalization — guarantees UI unsticks even if
                // drain hangs or network drops. Deadline only releases the UI; segments
                // uploaded before workP started are safe, the last chunk may still be local-only.
                var self = this;
                var FINALIZATION_DEADLINE_MS = 120000;
                var _dlTimerId = null;
                var deadlineP = new Promise(function (_, reject) {
                    _dlTimerId = setTimeout(function () {
                        reject(new Error('_finalization_deadline'));
                    }, FINALIZATION_DEADLINE_MS);
                });
                // Capture pipeline + session ID before starting workP. After deadline fires we null both
                // references immediately so a newer recording can't be stomped by late-completing callbacks.
                var finalPipeline = self._chunkPipeline;
                var finalSessionId = finalPipeline ? finalPipeline.getSessionId() : '';
                var hadChunkPipeline = !!finalPipeline;
                try {
                    var workP = (async function () {
                        if (typeof finalPipeline.ensureSessionComplete === 'function') {
                            await finalPipeline.ensureSessionComplete();
                        } else {
                            await finalPipeline.waitUntilIdle();
                        }
                        return await finalPipeline.fetchMerged();
                    })();
                    var merged = await Promise.race([workP, deadlineP]);
                    transcribeOk = !!(merged && String(merged).trim());
                    // P3: clear the 120s timer on success — the detached promise's reject won't
                    // actually break anything, but cancelling avoids a stale timer sitting around.
                    if (_dlTimerId) clearTimeout(_dlTimerId);
                } catch (dlErr) {
                    if (dlErr.message === '_finalization_deadline') {
                        console.warn('[ASR] Finalization deadline exceeded — releasing UI; audio retained locally when available');
                        try { this._reportAsrIncident({ stage: 'client.finalization', outcome: 'deadline_exceeded', traceId: '', message: 'ensureSessionComplete+fetchMerged exceeded ' + FINALIZATION_DEADLINE_MS + 'ms', ctx_state: (typeof window !== 'undefined' && window.app && window.app.audioPipelinePhase) || '' }); } catch (_) {}
                        transcribeOk = false;
                    } else {
                        throw dlErr;
                    }
                } finally {
                    if (_dlTimerId) clearTimeout(_dlTimerId);  // P3: clear timer on every exit path
                }
                // P2: only merge a worker 'error' when we have no successful transcript from fetchMerged.
                // A transient chunk error followed by a successful drain fallback must not force failure.
                if (!transcribeOk) {
                    try {
                        if (typeof window !== 'undefined' && window.app && window.app._recordingTranscribeOutcome) {
                            var workerOk = window.app._recordingTranscribeOutcome.transcribeOk;
                            if (workerOk === false) transcribeOk = false;
                        }
                    } catch (_) {}
                }
                // Null handler's pipeline and session ID so that a detached old pipeline
                // cannot emit transcribing or deliver a late through onStatus/onTranscript.
                self._chunkPipeline = null;
                self._asrChunkSessionId = '';
                try { if (typeof window !== 'undefined' && window.app) {
                    window.app._asrChunkPipeline = null;
                    window.app._asrChunkSessionId = '';
                } } catch (_) {}
                try {
                    if (typeof window !== 'undefined' && window.app) {
                        window.app._recordingTranscribeOutcome = { transcribeOk: transcribeOk };
                    }
                } catch (_) {}
            }

            var MIN_AUDIO_BYTES = 1000;
            if (audioBlob.size < MIN_AUDIO_BYTES) {
                this._asrStatus('error', 'No audio data captured. Please try recording again.');
                if (recoveryId && window.RecordingRecovery && typeof window.RecordingRecovery.deleteRecording === 'function') {
                    try { await window.RecordingRecovery.deleteRecording(recoveryId); } catch (_) {}
                }
                stream.getTracks().forEach(function (t) { t.stop(); });
                return;
            }

            var stoppedAt = Date.now();
            var durationSec = state.startedAt ? Math.max(0, (stoppedAt - state.startedAt) / 1000) : null;
            var backupFile = new File([audioBlob], localRecordingFileName, { type: outType });

            try {
                if (recoveryId && window.RecordingRecovery && typeof window.RecordingRecovery.finalizeRecording === 'function') {
                    await window.RecordingRecovery.finalizeRecording(recoveryId);
                }
            } catch (_) {}

            this._asrStatus('processing', hadChunkPipeline ? 'Finalizing…' : 'Transcribing recording…');
            var reportedErr = false;
            try {
                if (this.onAudioFileCallback) {
                    if (hadChunkPipeline) {
                        uploadOk = transcribeOk;
                        await this.onAudioFileCallback(backupFile, {
                            storeLatest: true,
                            chunkMode: true,
                            chunkPersistOk: transcribeOk,
                            fileName: backupFile.name,
                            recordingSessionId: finalSessionId || '',
                            durationSec: durationSec,
                            startedAt: state.startedAt ? new Date(state.startedAt).toISOString() : '',
                            stoppedAt: new Date(stoppedAt).toISOString(),
                        });
                    } else {
                        var savedRecording = await this.onAudioFileCallback(backupFile, {
                            storeLatest: true,
                            fileName: backupFile.name,
                            recordingSessionId: ctx.recoveryId || '',
                            durationSec: durationSec,
                            startedAt: state.startedAt ? new Date(state.startedAt).toISOString() : '',
                            stoppedAt: new Date(stoppedAt).toISOString(),
                        });
                        uploadOk = savedRecording === true || !!(savedRecording && savedRecording.ok);
                        var r = await this.onAudioFileCallback(backupFile, {
                            fullFileTranscribe: true,
                            fileName: backupFile.name,
                            recordingSessionId: ctx.recoveryId || '',
                            durationSec: durationSec,
                            startedAt: state.startedAt ? new Date(state.startedAt).toISOString() : '',
                            stoppedAt: new Date(stoppedAt).toISOString(),
                            segmentId: savedRecording && savedRecording.segmentId ? String(savedRecording.segmentId) : '',
                            retainedSegment: !!(savedRecording && savedRecording.segmentId && savedRecording.retained),
                        });
                        transcribeOk = r === true;
                    }
                }
            } catch (e) {
                reportedErr = true;
                this._asrStatus('error', 'Transcription failed: ' + String(e && e.message ? e.message : e));
            }

            if (transcribeOk) {
                this._asrStatus('done', 'Transcription complete');
            } else if (!reportedErr && hadChunkPipeline) {
                this._asrStatus('error', 'Transcription did not complete. Check your connection or tap Re-transcribe.');
            } else if (!reportedErr && !hadChunkPipeline) {
                this._asrStatus('error', 'Transcription did not complete. Check your connection or tap Re-transcribe.');
            }

            stream.getTracks().forEach(function (t) { t.stop(); });
        } finally {
            if (recoveryId && window.RecordingRecovery && typeof window.RecordingRecovery.deleteRecording === 'function') {
                if (uploadOk) {
                    try { await window.RecordingRecovery.deleteRecording(recoveryId); } catch (_) {}
                } else {
                    try { await window.RecordingRecovery.markStopped(recoveryId); } catch (_) {}
                }
            }
            this._sessionBackupChunks = [];
            if (this._chunkPipeline) {
                this._chunkPipeline = null;
                try {
                    if (typeof window !== 'undefined' && window.app) {
                        window.app._asrChunkPipeline = null;
                    }
                } catch (_) {}
            }
            this._recordingLock = false;
            this._recordingSession = null;
            this.isRecording = false;
            this.onRecordingStop();
            if (!this.shouldKeepRecording) {
                try { this._releaseWakeLock && this._releaseWakeLock(); } catch (e) {}
            }
        }
    }

    async startAudioRecording() {
        // Phase guard must run BEFORE the lock so a failed check doesn't
        // leave _recordingLock = true permanently (bricking all future recordings).
        if (typeof window !== 'undefined' && window.app && window.app.audioPipelinePhase) {
            const ph = window.app.audioPipelinePhase;
            if (ph === 'transcribing' || ph === 'generating_note') {
                throw new Error('Wait for transcription or note generation to finish before recording again.');
            }
        }
        // Re-entrancy guard: synchronous check+set before any await so a second tap
        // (or OS interruption, or consent-audio await) cannot re-enter mid-init.
        // The lock is cleared in stopAudioRecording(), onerror, and onstop-finally.
        if (this._recordingLock) {
            throw new Error('Already recording');
        }
        this._recordingLock = true;
        const gen = ++this._recordingGeneration;  // invalidated by stopAudioRecording

        try {
            this.shouldKeepRecording = true;
            this._asrDebugStartTs = Date.now();
            try { if ('wakeLock' in navigator) { this._requestWakeLock && this._requestWakeLock(); } } catch (e) {}
            const stream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    echoCancellation: true,
                    noiseSuppression: true,
                    sampleRate: 44100
                }
            });

            // Abort if stop was called during the getUserMedia await (tap→stop race).
            // stopAudioRecording() increments _recordingGeneration to invalidate in-flight starts.
            if (this._recordingGeneration !== gen) {
                console.warn('[Audio] Start cancelled — superseded by later start/stop');
                // Only clean up OUR stream. Shared state (_recordingLock, isListening,
                // phase) belongs to whoever bumped the generation (stopAudioRecording or
                // a newer start). Touching it here would clobber a legitimate new recording.
                stream.getTracks().forEach(function (t) { t.stop(); });
                return;
            }
            this._asrDbg('mic.getUserMedia.ok', {
                tracks: (stream && stream.getAudioTracks) ? stream.getAudioTracks().map(t => ({ id: t.id, enabled: t.enabled, muted: t.muted, readyState: t.readyState })) : [],
            });

            this.audioChunks = [];

            // Backup whole-file recorder (WebM)
            let options = {};
            if (MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) {
                options = { mimeType: 'audio/webm;codecs=opus' };
            } else if (MediaRecorder.isTypeSupported('audio/webm')) {
                options = { mimeType: 'audio/webm' };
            } else {
                options = {};
            }
            this.mediaRecorder = new MediaRecorder(stream, options);
            this._asrDbg('mediaRecorder.created', { mimeType: this.mediaRecorder.mimeType, options });
            // Recovery: persist MediaRecorder blob segments to IndexedDB (user+encounter scope, TTL).
            let recoveryId = null;
            let recoverySeq = 0;
            const scopeEncForName = (window.RecordingRecovery && window.RecordingRecovery.safeEncounterId)
                ? window.RecordingRecovery.safeEncounterId()
                : '';
            const localRecordingFileName = `enc_${String(scopeEncForName || 'none').slice(0, 8)}_recording_${Date.now()}_${Math.random().toString(16).slice(2)}.webm`;
            try {
                if (window.RecordingRecovery && typeof window.RecordingRecovery.startRecordingSession === 'function') {
                    const scopeUser = window.RecordingRecovery.safeUserKey ? window.RecordingRecovery.safeUserKey() : '';
                    const scopeEnc = scopeEncForName;
                    const rr = await window.RecordingRecovery.startRecordingSession({
                        mimeType: options?.mimeType || this.mediaRecorder.mimeType || 'audio/webm',
                        fileName: localRecordingFileName,
                        userKey: scopeUser,
                        encounterId: scopeEnc,
                        baselineText: (window.app && typeof window.app.asrBaselineBeforeRecording === 'string')
                            ? window.app.asrBaselineBeforeRecording
                            : '',
                    });
                    if (rr && rr.ok) {
                        recoveryId = rr.recordingId;
                    } else if (rr && rr.reason === 'missing_scope') {
                        console.warn('[Audio] IndexedDB backup disabled — no active user/encounter. Recording will not survive a page reload.');
                    }
                }
            } catch (_) {
                recoveryId = null;
            }

            const state = {
                stream,
                hadErrors: false,
                startedAt: Date.now(),
            };
            this._recordingSession = state;

            // MediaRecorder used as the encounter-wide WebM backup.
            this._sessionBackupChunks = [];
            state.segmentStartedAt = Date.now();
            const handlerCtx = {
                recoveryId,
                recoverySeqRef: { value: recoverySeq },
                localRecordingFileName,
                recorderOptions: options,
                gen,
            };
            this._bindMediaRecorderHandlers(state, handlerCtx);

            // Start the backup recorder with a 5-second timeslice so IndexedDB
            // receives periodic chunks for crash recovery.
            // Second generation check: a stop during the startRecordingSession() await
            // (between getUserMedia and here) invalidates this start.
            if (this._recordingGeneration !== gen) {
                console.warn('[Audio] Start cancelled during setup — superseded');
                stream.getTracks().forEach(function (t) { t.stop(); });
                return;
            }
            this.mediaRecorder.start(5000);
            this.isRecording = true;
            if (this._chunkPipeline) {
                this._scheduleChunkSegmentRotation();
            }

            // Auto-stop after 30 minutes to prevent infinite recordings (e.g. if the
            // stop path breaks or the user forgets). The audio up to this point is saved.
            this._recordingTimeout = setTimeout(() => {
                if (this.isRecording && this.mediaRecorder && this.mediaRecorder.state === 'recording') {
                    console.warn('[Audio] Recording timeout (30 min) — auto-stopping');
                    this._asrStatus('error', 'Recording stopped (30 min limit). Your audio has been saved.');
                    this._reportAsrIncident({
                        stage: 'client.recording_timeout',
                        outcome: 'aborted',
                        trace_id: '',
                        message: 'Auto-stopped after 30 min recording limit',
                    });
                    this.stopAudioRecording();
                }
            }, 30 * 60 * 1000);

            try {
                this._startInputLevelMonitor(stream);
            } catch (e) {
                console.warn('[Audio] input level monitor failed:', e);
            }
            this.onRecordingStart();
            this._asrDbg('recording.started', { mimeType: this.mediaRecorder.mimeType });
            
        } catch (error) {
            console.error('Audio recording error:', error);
            if (this._recordingTimeout) {
                clearTimeout(this._recordingTimeout);
                this._recordingTimeout = null;
            }
            this._recordingLock = false;
            this.onRecordingError(error);
            this._asrDbg('recording.start.failed', { message: String(error && error.message ? error.message : error) });
            this._reportAsrIncident({
                stage: 'client.recording_start_failed',
                outcome: 'error',
                trace_id: '',
                message: String(error && error.message ? error.message : error),
            });
            throw error;
        }
    }

    // Stop audio recording
    stopAudioRecording() {
        ++this._recordingGeneration;  // invalidate any in-flight start (tap→stop race fix)
        this._clearChunkSegmentRotation();
        if (this._recordingTimeout) {
            clearTimeout(this._recordingTimeout);
            this._recordingTimeout = null;
        }
        this._recordingLock = false;
        try {
            this._stopInputLevelMonitor();
        } catch (_) {}
        // Keep direct callers safe: stopping the MediaRecorder also means
        // this handler is no longer actively listening for ASR input.
        this.isListening = false;
        if (this.mediaRecorder && this.mediaRecorder.state === 'recording') {
            this.mediaRecorder.stop();
            // onstop will fire → onRecordingStop runs there
        } else {
            // No live MediaRecorder (e.g. stop during init, or double-tap race).
            // onstop won't fire, so reset the UI phase directly.
            this.onRecordingStop();
        }
        this.isRecording = false;
        this.shouldKeepRecording = false;
        try { this._releaseWakeLock && this._releaseWakeLock(); } catch (e) {}
    }

    /**
     * Force-reset all recording state. Emergency escape hatch when the pipeline
     * is stuck (button frozen, encounter switching blocked).
     * Safe to call at any time — idempotent, never throws.
     */
    forceResetRecording() {
        try {
            ++this._recordingGeneration;  // cancel any in-flight start
            this._clearChunkSegmentRotation();
            if (this._recordingTimeout) {
                clearTimeout(this._recordingTimeout);
                this._recordingTimeout = null;
            }
            this._recordingLock = false;
            this.isListening = false;
            this.isRecording = false;
            this.shouldKeepRecording = false;
            this.shouldKeepListening = false;
            try { this._stopInputLevelMonitor(); } catch (_) {}
            try { this._releaseWakeLock && this._releaseWakeLock(); } catch (_) {}
            if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') {
                // Detach onstop BEFORE stopping. stop() fires onstop asynchronously;
                // onstop would then build a blob from the audioChunks we are about
                // to clear, see it as "too small", and delete the IndexedDB
                // recovery copy — destroying a full recording. With onstop
                // detached the recovery session is left intact, and
                // server recovery salvages it on the next opportunity.
                try { this.mediaRecorder.onstop = null; } catch (_) {}
                try { this.mediaRecorder.stop(); } catch (_) {}
            }
            if (this._recordingSession && this._recordingSession.stream) {
                try {
                    this._recordingSession.stream.getTracks().forEach(function(t) { t.stop(); });
                } catch (_) {}
            }
            this._recordingSession = null;
            this.audioChunks = [];
            if (typeof window !== 'undefined' && window.app) {
                try { window.app.audioPipelinePhase = 'idle'; } catch (_) {}
            }
            if (window.CNGAudioUI && typeof window.CNGAudioUI.syncRecordingPipelineUi === 'function') {
                try { window.CNGAudioUI.syncRecordingPipelineUi('idle'); } catch (_) {}
            }
        } catch (_) {}
    }

    // Get browser-specific guidance
    getBrowserGuidance() {
        const caps = this.capabilities;
        
        if (caps.isAndroid && caps.isChrome) {
            return {
                speechAvailable: true,
                recordingAvailable: true,
                message: '✅ Android Chrome: Full audio support available',
                recommendations: ['Voice recognition works great', 'Audio recording supported', 'All features available']
            };
        }
        
        if (caps.isAndroid && caps.isFirefox) {
            return {
                speechAvailable: true,
                recordingAvailable: true,
                message: '✅ Android Firefox: Full audio support available',
                recommendations: ['Voice recognition works great', 'Audio recording supported', 'All features available']
            };
        }
        
        if (caps.isAndroid && caps.isSamsung) {
            return {
                speechAvailable: true,
                recordingAvailable: true,
                message: '✅ Samsung Browser: Full audio support available',
                recommendations: ['Voice recognition works great', 'Audio recording supported', 'All features available']
            };
        }
        
        if (caps.isIOS && caps.isSafari) {
            return {
                speechAvailable: true,
                recordingAvailable: true,
                message: '✅ iOS Safari: Full audio support available',
                recommendations: ['Voice recognition works great', 'Audio recording supported (iOS 14.3+)', 'All features available']
            };
        }
        
        if (caps.isChromeiOS) {
            return {
                speechAvailable: false,
                recordingAvailable: caps.canUseAudioRecording,
                message: '⚠️ Chrome iOS: Limited audio support',
                recommendations: [
                    'Voice recognition not supported',
                    caps.canUseAudioRecording ? 'Audio recording may work' : 'Audio recording limited',
                    'Use file upload for best experience',
                    'Consider switching to Safari for full features'
                ]
            };
        }
        
        if (caps.isFirefoxiOS) {
            return {
                speechAvailable: false,
                recordingAvailable: caps.canUseAudioRecording,
                message: '⚠️ Firefox iOS: Limited audio support',
                recommendations: [
                    'Voice recognition not supported',
                    caps.canUseAudioRecording ? 'Audio recording may work' : 'Audio recording limited',
                    'Use file upload for best experience',
                    'Consider switching to Safari for full features'
                ]
            };
        }
        
        return {
            speechAvailable: caps.canUseSpeechRecognition,
            recordingAvailable: caps.canUseAudioRecording,
            message: 'Generic browser: Basic support',
            recommendations: ['File upload always works', 'Check browser permissions for microphone']
        };
    }

    // Event handlers (override these)
    onSpeechStart() {
        console.log('Speech recognition started');
    }
    
    onSpeechStop() {
        console.log('Speech recognition stopped');
    }
    
    onSpeechError(error) {
        console.error('Speech recognition error:', error);
    }
    
    onRecordingStart() {
        console.log('Audio recording started');
    }
    
    onRecordingStop() {
        console.log('Audio recording stopped');
    }
    
    onRecordingError(error) {
        console.error('Audio recording error:', error);
    }

    // Helper methods
    getSpeechUnavailableReason() {
        if (this.capabilities.isChromeiOS) {
            return 'Chrome iOS does not support Web Speech API';
        }
        if (this.capabilities.isFirefoxiOS) {
            return 'Firefox iOS does not support Web Speech API';
        }
        if (this.capabilities.isIOS && !this.capabilities.isSafari) {
            return 'Only Safari supports speech recognition on iOS';
        }
        if (this.capabilities.isAndroid && !this.capabilities.isChrome && !this.capabilities.isFirefox && !this.capabilities.isSamsung) {
            return 'Try Chrome, Firefox, or Samsung Browser on Android';
        }
        return 'Speech recognition not supported in this browser';
    }
    
    getRecordingUnavailableReason() {
        if (!this.capabilities.hasGetUserMedia) {
            return 'getUserMedia API not supported';
        }
        if (!this.capabilities.hasMediaRecorder) {
            return 'MediaRecorder API not supported';
        }
        return 'Audio recording not available';
    }

    // Set callbacks
    setTranscriptionCallback(callback) {
        this.onTranscriptionCallback = callback;
    }
    
    setAudioFileCallback(callback) {
        this.onAudioFileCallback = callback;
    }

    setInputSilenceCallback(callback) {
        this.onInputSilenceCallback = typeof callback === 'function' ? callback : null;
    }
}

// Global function to initialize universal audio
function initUniversalAudio() {
    window.universalAudio = new UniversalAudioHandler();
    return window.universalAudio;
}

// Export for module usage
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { UniversalAudioHandler, initUniversalAudio };
}

// Minimal Wake Lock helpers (no-op if unsupported)
UniversalAudioHandler.prototype._requestWakeLock = async function() {
    try {
        if ('wakeLock' in navigator && !this.wakeLock) {
            this.wakeLock = await navigator.wakeLock.request('screen');
            document.addEventListener('visibilitychange', this._reacquireWakeLock.bind(this));
        }
    } catch (e) { /* ignore */ }
};

UniversalAudioHandler.prototype._releaseWakeLock = function() {
    try { if (this.wakeLock) { this.wakeLock.release(); this.wakeLock = null; } } catch (e) { }
    document.removeEventListener('visibilitychange', this._reacquireWakeLock);
};

UniversalAudioHandler.prototype._reacquireWakeLock = async function() {
    if (document.visibilityState === 'visible' && this.shouldKeepListening) {
        try { this.wakeLock = await navigator.wakeLock.request('screen'); } catch (e) { /* ignore */ }
    }
};

/** RMS threshold below which we treat input as silence (~still air / muted). Tunable; avoid firing on soft speech. */
UniversalAudioHandler.prototype._INPUT_SILENCE_RMS = 0.014;
/** Milliseconds of continuous silence before alerting. */
UniversalAudioHandler.prototype._INPUT_SILENCE_HOLD_MS = 10000;
/** Milliseconds of continuous silence before auto-stopping the recording. */
UniversalAudioHandler.prototype._INPUT_SILENCE_ABORT_MS = 60000;

UniversalAudioHandler.prototype._startInputLevelMonitor = function(stream) {
    this._stopInputLevelMonitor();
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx || !stream) return;

    try {
        const ctx = new AudioCtx();
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 2048;
        analyser.smoothingTimeConstant = 0.85;
        const source = ctx.createMediaStreamSource(stream);
        source.connect(analyser);

        this._inputAudioCtx = ctx;
        this._inputAnalyser = analyser;
        this._inputSilentAccumMs = 0;
        this._inputLastTickMs = performance.now();
        this._inputSilenceAlerted = false;

        const buf = new Float32Array(analyser.fftSize);
        const threshold = this._INPUT_SILENCE_RMS;
        const holdMs = this._INPUT_SILENCE_HOLD_MS;

        const tick = () => {
            if (!this.isRecording || !this._inputAnalyser) return;
            const now = performance.now();
            const dt = Math.min(400, Math.max(0, now - this._inputLastTickMs));
            this._inputLastTickMs = now;

            try {
                this._inputAnalyser.getFloatTimeDomainData(buf);
            } catch (_) {
                this._inputLevelRaf = requestAnimationFrame(tick);
                return;
            }
            let sum = 0;
            for (let i = 0; i < buf.length; i++) {
                const v = buf[i];
                sum += v * v;
            }
            const rms = Math.sqrt(sum / buf.length);

            if (rms < threshold) {
                this._inputSilentAccumMs += dt;
            } else {
                this._inputSilentAccumMs = 0;
                if (this._inputSilenceAlerted) {
                    this._inputSilenceAlerted = false;
                    if (this.onInputSilenceCallback) {
                        try {
                            this.onInputSilenceCallback(false);
                        } catch (_) {}
                    }
                }
            }

            if (!this._inputSilenceAlerted && this._inputSilentAccumMs >= holdMs) {
                this._inputSilenceAlerted = true;
                if (this.onInputSilenceCallback) {
                    try {
                        this.onInputSilenceCallback(true);
                    } catch (_) {}
                }
            }

            // Hard abort: sustained silence for too long → stop recording automatically.
            // This catches dead mics, muted devices, and iOS Safari silent-track bugs
            // before the doctor wastes minutes recording nothing.
            if (this._inputSilentAccumMs >= this._INPUT_SILENCE_ABORT_MS) {
                console.warn('[Audio] Sustained silence (' + Math.round(this._inputSilentAccumMs / 1000) + 's) — auto-stopping');
                if (this._asrStatus) {
                    try { this._asrStatus('error', 'Recording stopped — no microphone input detected.'); } catch (_) {}
                }
                this._reportAsrIncident({
                    stage: 'client.silence_abort',
                    outcome: 'aborted',
                    trace_id: '',
                    message: 'Auto-stopped after ' + Math.round(this._inputSilentAccumMs / 1000) + 's of sustained silence',
                });
                this.stopAudioRecording();
                return;
            }

            this._inputLevelRaf = requestAnimationFrame(tick);
        };

        ctx.resume().then(() => {
            if (this.isRecording && this._inputAnalyser) {
                this._inputLevelRaf = requestAnimationFrame(tick);
            }
        }).catch(() => {
            if (this.isRecording && this._inputAnalyser) {
                this._inputLevelRaf = requestAnimationFrame(tick);
            }
        });
    } catch (e) {
        console.warn('[Audio] _startInputLevelMonitor:', e);
    }
};

UniversalAudioHandler.prototype._stopInputLevelMonitor = function() {
    if (this._inputLevelRaf) {
        cancelAnimationFrame(this._inputLevelRaf);
        this._inputLevelRaf = null;
    }
    try {
        if (this._inputAudioCtx && this._inputAudioCtx.state !== 'closed') {
            this._inputAudioCtx.close();
        }
    } catch (_) {}
    this._inputAudioCtx = null;
    this._inputAnalyser = null;
    this._inputSilentAccumMs = 0;
    this._inputLastTickMs = 0;
    if (this._inputSilenceAlerted && this.onInputSilenceCallback) {
        try {
            this.onInputSilenceCallback(false);
        } catch (_) {}
    }
    this._inputSilenceAlerted = false;
};
