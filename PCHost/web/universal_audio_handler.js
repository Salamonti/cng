// C:\PCHost\web\universal_audio_handler.js
/**
 * Universal Audio Handler for Clinical Note Generator
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
        this.onTranscriptionCallback = null;
        this.onAudioFileCallback = null;
        this.wakeLock = null; // screen wake lock handle
        
        // WebSocket streaming (legacy naming)
        this.ws = null;
        this.wsConnected = false;
        this.wsSessionId = null;
        this.useWebSocket = false;
        this.wsFallbackEnabled = true; // fallback to HTTP POST if WS fails
        
        // New ASR streaming (Codex architecture)
        this.asrSocket = null;
        this.asrConnected = false;
        this.asrFailed = false;
        this.onAsrStatusCallback = null;
        this.liveAudioContext = null;
        this.liveProcessor = null;
        this.liveSource = null;
        this.liveStream = null;
    }

    _extensionFromMime(mimeType) {
        const mt = (mimeType || '').toLowerCase();
        if (mt.includes('webm')) return 'webm';
        if (mt.includes('ogg')) return 'ogg';
        if (mt.includes('mp4') || mt.includes('m4a')) return 'm4a';
        if (mt.includes('wav')) return 'wav';
        return 'webm';
    }

    setAsrStatusCallback(callback) {
        this.onAsrStatusCallback = callback;
    }

    _asrStatus(state, detail = '') {
        if (this.onAsrStatusCallback) this.onAsrStatusCallback(state, detail);
    }

    _buildAsrWsUrl(token) {
        const apiBase = (window.getApiBase ? window.getApiBase(window.app?.settings?.serverUrl || '/api') : '/api');
        const base = new URL(apiBase, window.location.origin);
        const proto = base.protocol === 'https:' ? 'wss:' : 'ws:';
        return `${proto}//${base.host}${base.pathname.replace(/\/$/, '')}/asr/ws?access_token=${encodeURIComponent(token)}`;
    }

    _connectAsrSocket(token) {
        return new Promise((resolve, reject) => {
            const ws = new WebSocket(this._buildAsrWsUrl(token), ['cng.asr.v1']);
            const timeout = setTimeout(() => reject(new Error('asr_ws_timeout')), 8000);
            ws.onopen = () => {
                clearTimeout(timeout);
                this.asrSocket = ws;
                this.asrConnected = true;
                this.asrFailed = false;
                this._asrStatus('streaming', 'Live transcription connected');
                resolve();
            };
            ws.onmessage = (event) => {
                try {
                    const msg = JSON.parse(event.data);
                    if (msg.type === 'ready') {
                        this._asrStatus('streaming', 'Live transcription connected');
                        return;
                    }
                    if (msg.type === 'error') {
                        this.asrConnected = false;
                        this.asrFailed = true;
                        this._asrStatus('fallback', msg.detail || 'Live stream failed, using HTTP fallback');
                        try { ws.close(); } catch (_) {}
                        return;
                    }
                    if ((msg.type === 'partial' || msg.type === 'final') && msg.text && this.onTranscriptionCallback) {
                        this.onTranscriptionCallback(msg.text, msg.type === 'final' ? 'final' : 'interim');
                    }
                } catch (_) {}
            };
            ws.onerror = () => {
                this.asrConnected = false;
                this.asrFailed = true;
                this._asrStatus('fallback', 'Live stream failed, using HTTP fallback');
            };
            ws.onclose = () => {
                this.asrConnected = false;
                if (this.isListening) {
                    this.asrFailed = true;
                    this._asrStatus('fallback', 'Live stream disconnected, using HTTP fallback');
                }
            };
        });
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
        
        // Determine speech recognition capabilities
        if (capabilities.isAndroid) {
            capabilities.canUseSpeechRecognition = capabilities.isChrome || capabilities.isFirefox || capabilities.isSamsung;
        } else if (capabilities.isIOS) {
            capabilities.canUseSpeechRecognition = capabilities.isSafari; // Only Safari on iOS
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

    // Initialize speech recognition
    initSpeechRecognition() {
        if (!this.capabilities.canUseSpeechRecognition) {
            return {
                available: false,
                reason: this.getSpeechUnavailableReason()
            };
        }

        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        this.speechRecognition = new SpeechRecognition();
        
        // Configure for mobile compatibility
        this.speechRecognition.continuous = true; // Better for mobile
        this.speechRecognition.interimResults = true;
        this.speechRecognition.lang = 'en-US';

        // Android-specific settings for better performance
        if (this.capabilities.isAndroid) {
            this.speechRecognition.maxAlternatives = 1;
        }

        // Handle longer pauses - don't auto-stop on speech end
        this.speechRecognition.onspeechend = null;
        
        // Event handlers
        this.speechRecognition.onstart = () => {
            this.isListening = true;
            this.onSpeechStart();
        };
        
        this.speechRecognition.onresult = (event) => {
            let transcript = '';
            let finalTranscript = '';
            
            // Only process NEW results (avoid repetition)
            for (let i = event.resultIndex; i < event.results.length; i++) {
                if (event.results[i].isFinal) {
                    finalTranscript += event.results[i][0].transcript;
                } else {
                    transcript += event.results[i][0].transcript;
                }
            }
            
            const textToAdd = finalTranscript || transcript;
            if (textToAdd.trim() && this.onTranscriptionCallback) {
                this.onTranscriptionCallback(textToAdd.trim(), finalTranscript ? 'final' : 'interim');
            }
            
            // ✅ Auto-restart INSIDE the onresult function
            if (finalTranscript && this.isListening) {
                setTimeout(() => {
                    if (this.isListening) {
                        try {
                            this.speechRecognition.start();
                        } catch (e) {
                            // Already running, ignore
                        }
                    }
                }, 100);
            }
        };
    
        
        this.speechRecognition.onerror = (event) => {
            this.onSpeechError(event.error);
            if (this.shouldKeepListening) {
                this.isListening = false;
                this.onSpeechStop();
                setTimeout(() => { try { this.speechRecognition.start(); } catch (e) {} }, 400);
            } else {
                this.stopSpeechRecognition();
            }
        };
        
        this.speechRecognition.onend = () => {
            if (this.shouldKeepListening) {
                this.isListening = false;
                this.onSpeechStop();
                setTimeout(() => { try { this.speechRecognition.start(); } catch (e) {} }, 250);
            } else {
                this.stopSpeechRecognition();
            }
        };
        
        return { available: true, method: 'speech_recognition' };
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

    // Start speech recognition - NOW USES SERVER-SIDE TRANSCRIPTION
    async startSpeechRecognition() {
        console.log('[AudioHandler] Using WebSocket ASR with HTTP fallback');

        try {
            this.shouldKeepListening = true;
            this.isListening = true;
            this.dictationChunks = [];

            try { if ('wakeLock' in navigator) { this._requestWakeLock && this._requestWakeLock(); } } catch (e) {}

            const token = (window.getAuthToken && window.getAuthToken()) || '';
            if (token) {
                this._asrStatus('connecting', 'Connecting live transcription...');
                try { await this._connectAsrSocket(token); } catch (_) { this.asrFailed = true; }
            }

            const stream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    echoCancellation: true,
                    noiseSuppression: true,
                    sampleRate: 16000  // Whisper prefers 16kHz
                }
            });
            this.liveStream = stream;

            this.dictationChunks = [];

            // Use WAV format for best compatibility
            let options = { mimeType: 'audio/webm' };
            if (!MediaRecorder.isTypeSupported('audio/webm')) {
                if (MediaRecorder.isTypeSupported('audio/wav')) {
                    options = { mimeType: 'audio/wav' };
                } else {
                    options = {};
                }
            }

            this.dictationRecorder = new MediaRecorder(stream, options);

            this.dictationRecorder.ondataavailable = async (event) => {
                if (event.data.size > 0) {
                    this.dictationChunks.push(event.data);
                    if (this.useWebSocket && this.wsConnected) {
                        await this.sendAudioChunk(event.data, this.dictationChunks);
                    }
                }
            };

            this.dictationRecorder.start(1000); // keep fallback recording

            // Send PCM chunks over WS (~256 ms)
            if (this.asrConnected) {
                this.liveAudioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
                this.liveSource = this.liveAudioContext.createMediaStreamSource(stream);
                this.liveProcessor = this.liveAudioContext.createScriptProcessor(4096, 1, 1);
                this.liveProcessor.onaudioprocess = (e) => {
                    if (!this.asrSocket || this.asrSocket.readyState !== WebSocket.OPEN) return;
                    const f32 = e.inputBuffer.getChannelData(0);
                    const i16 = new Int16Array(f32.length);
                    for (let i = 0; i < f32.length; i++) i16[i] = Math.max(-32768, Math.min(32767, f32[i] * 32767));
                    this.asrSocket.send(i16.buffer);
                };
                this.liveSource.connect(this.liveProcessor);
                this.liveProcessor.connect(this.liveAudioContext.destination);
            }

            this.onSpeechStart();

        } catch (error) {
            console.error('Failed to start server-side speech recognition:', error);
            this.isListening = false;
            throw error;
        }
    }

    // Stop speech recognition
    async stopSpeechRecognition() {
        this.shouldKeepListening = false;
        this.isListening = false;
        
        if (this.asrSocket && this.asrSocket.readyState === WebSocket.OPEN) {
            this.asrSocket.send(JSON.stringify({ type: 'stop' }));
            this.asrSocket.close();
        }
        this.asrSocket = null;
        this.asrConnected = false;
        
        if (this.liveProcessor) this.liveProcessor.disconnect();
        if (this.liveSource) this.liveSource.disconnect();
        if (this.liveAudioContext) await this.liveAudioContext.close();
        this.liveProcessor = null;
        this.liveSource = null;
        this.liveAudioContext = null;

        if (this.dictationRecorder && this.dictationRecorder.state !== 'inactive') {
            // Stop recorder and send final chunk
            this.dictationRecorder.stop();

            // Wait for final chunk and transcribe
            await new Promise(resolve => {
                this.dictationRecorder.onstop = async () => {
                    if (this.dictationChunks.length > 0 && (!this.asrConnected || this.asrFailed)) {
                        const outType = this.dictationRecorder.mimeType || this.dictationChunks[0]?.type || 'audio/webm';
                        const ext = this._extensionFromMime(outType);
                        const audioBlob = new Blob(this.dictationChunks, { type: outType });
                        const audioFile = new File([audioBlob], `dictation_${Date.now()}.${ext}`, {
                            type: outType
                        });

                        // HTTP fallback path (existing queue-on-failure remains in index.html)
                        if (this.onAudioFileCallback) {
                            this.onAudioFileCallback(audioFile);
                        }
                    }

                    // Clean up stream
                    if (this.dictationRecorder && this.dictationRecorder.stream) {
                        this.dictationRecorder.stream.getTracks().forEach(track => track.stop());
                    }

                    this.dictationChunks = [];
                    resolve();
                };
            });
        }

        this.onSpeechStop();
        try { this._releaseWakeLock && this._releaseWakeLock(); } catch (e) {}
    }

    // Start audio recording
    async startAudioRecording() {
        if (this.isRecording) {
            throw new Error('Already recording');
        }

        try {
            this.shouldKeepRecording = true;
            try { if ('wakeLock' in navigator) { this._requestWakeLock && this._requestWakeLock(); } } catch (e) {}
            const stream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    echoCancellation: true,
                    noiseSuppression: true,
                    sampleRate: 44100
                }
            });

            this.audioChunks = [];

            // Use compatible audio format
            let options = { mimeType: 'audio/webm' };
            if (!MediaRecorder.isTypeSupported('audio/webm')) {
                if (MediaRecorder.isTypeSupported('audio/mp4')) {
                    options = { mimeType: 'audio/mp4' };
                } else if (MediaRecorder.isTypeSupported('audio/wav')) {
                    options = { mimeType: 'audio/wav' };
                } else {
                    options = {}; // Use default
                }
            }

            this.mediaRecorder = new MediaRecorder(stream, options);

            this.mediaRecorder.ondataavailable = async (event) => {
                if (event.data.size > 0) {
                    // Always accumulate for fallback
                    this.audioChunks.push(event.data);
                    // Try to send via WebSocket if connected
                    if (this.useWebSocket && this.wsConnected) {
                        await this.sendAudioChunk(event.data, this.audioChunks);
                    }
                }
            };

            this.mediaRecorder.onstop = () => {
                const outType = this.mediaRecorder.mimeType || this.audioChunks[0]?.type || 'audio/webm';
                const ext = this._extensionFromMime(outType);
                const audioBlob = new Blob(this.audioChunks, { type: outType });
                const audioFile = new File([audioBlob], `recording_${Date.now()}.${ext}`, {
                    type: outType
                });

                // Call callback with recorded file
                if (this.onAudioFileCallback) {
                    this.onAudioFileCallback(audioFile);
                }

                // Clean up stream
                stream.getTracks().forEach(track => track.stop());
                
                this.isRecording = false;
                this.onRecordingStop();

                // If user intends to keep recording, immediately restart a new segment
                if (this.shouldKeepRecording) {
                    setTimeout(() => { try { this.startAudioRecording(); } catch (e) {} }, 200);
                } else {
                    try { this._releaseWakeLock && this._releaseWakeLock(); } catch (e) {}
                }
            };
            // Attempt to recover on recorder errors
            if (this.mediaRecorder) {
                this.mediaRecorder.onerror = () => {
                    this.isRecording = false;
                    this.onRecordingError(new Error('Recorder error'));
                    if (this.shouldKeepRecording) {
                        setTimeout(() => { try { this.startAudioRecording(); } catch (e) {} }, 400);
                    } else {
                        try { this._releaseWakeLock && this._releaseWakeLock(); } catch (e) {}
                    }
                };
            }
            
            this.mediaRecorder.start();
            this.isRecording = true;
            this.onRecordingStart();
            
        } catch (error) {
            console.error('Audio recording error:', error);
            this.onRecordingError(error);
            throw error;
        }
    }

    // Stop audio recording
    stopAudioRecording() {
        if (this.mediaRecorder && this.mediaRecorder.state === 'recording') {
            this.mediaRecorder.stop();
        }
        this.isRecording = false;
        this.shouldKeepRecording = false;
        try { this._releaseWakeLock && this._releaseWakeLock(); } catch (e) {}
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

// WebSocket streaming methods
UniversalAudioHandler.prototype.connectWebSocket = async function(token) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            console.log('[ASR WS] Already connected');
            return;
        }
        
        // Build WebSocket URL with token
        const apiBase = window.app?.settings?.serverUrl || '/api';
        const wsUrl = apiBase.replace(/^http/, 'ws') + '/asr/ws';
        const url = new URL(wsUrl, window.location.href);
        url.searchParams.set('access_token', token);
        
        try {
            this.ws = new WebSocket(url.toString(), ['cng.asr.v1']);
            this.ws.binaryType = 'arraybuffer';
            this.ws.onopen = this._handleWebSocketOpen.bind(this);
            this.ws.onmessage = this._handleWebSocketMessage.bind(this);
            this.ws.onerror = this._handleWebSocketError.bind(this);
            this.ws.onclose = this._handleWebSocketClose.bind(this);
            
            // Wait for connection or timeout
            await new Promise((resolve, reject) => {
                const timeout = setTimeout(() => {
                    reject(new Error('WebSocket connection timeout'));
                }, 5000);
                this.ws.onopen = () => {
                    clearTimeout(timeout);
                    resolve();
                };
                this.ws.onerror = (err) => {
                    clearTimeout(timeout);
                    reject(err);
                };
            });
            
            this.wsConnected = true;
            this.useWebSocket = true;
            console.log('[ASR WS] Connected successfully');
        } catch (error) {
            console.error('[ASR WS] Connection failed:', error);
            this.wsConnected = false;
            this.useWebSocket = false;
            if (this.wsFallbackEnabled) {
                console.log('[ASR WS] Falling back to HTTP POST');
            }
            throw error;
        }
    }

UniversalAudioHandler.prototype._handleWebSocketOpen = function(event) {
    console.log('[ASR WS] Connection opened');
    this.wsConnected = true;
};

UniversalAudioHandler.prototype._handleWebSocketMessage = function(event) {
    try {
        const data = JSON.parse(event.data);
        if (data.type === 'partial' && data.text && this.onTranscriptionCallback) {
            // Send interim transcription to callback
            this.onTranscriptionCallback(data.text, 'interim');
        } else if (data.type === 'ready') {
            console.log('[ASR WS] Whisper stream ready');
        }
    } catch (e) {
        console.error('[ASR WS] Failed to parse message:', e);
    }
};

UniversalAudioHandler.prototype._handleWebSocketError = function(event) {
    console.error('[ASR WS] WebSocket error:', event);
    this.wsConnected = false;
    this.useWebSocket = false;
};

UniversalAudioHandler.prototype._handleWebSocketClose = function(event) {
    console.log('[ASR WS] Connection closed:', event.code, event.reason);
    this.wsConnected = false;
    this.useWebSocket = false;
    this.ws = null;
};

UniversalAudioHandler.prototype.disconnectWebSocket = async function() {
    if (this.ws) {
        this.ws.close(1000, 'normal');
        this.ws = null;
    }
    this.wsConnected = false;
    this.useWebSocket = false;
};

UniversalAudioHandler.prototype.sendAudioChunk = async function(chunk, fallbackArray = this.audioChunks) {
        if (!this.wsConnected || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
            // Fallback: accumulate chunk for HTTP POST
            if (!fallbackArray) fallbackArray = [];
            fallbackArray.push(chunk);
            return false;
        }
        
        try {
            // Convert Blob to ArrayBuffer for WebSocket
            const arrayBuffer = await chunk.arrayBuffer();
            this.ws.send(arrayBuffer);
            return true;
        } catch (error) {
            console.error('[ASR WS] Failed to send chunk:', error);
            this.wsConnected = false;
            this.useWebSocket = false;
            // Fallback: accumulate chunk
            if (!fallbackArray) fallbackArray = [];
            fallbackArray.push(chunk);
            return false;
        }
    };

UniversalAudioHandler.prototype._startPcmStreaming = function(stream) {
    if (!this.asrConnected || !this.asrSocket) return;
    
    try {
        const audioContext = new (window.AudioContext || window.webkitAudioContext)({
            sampleRate: 16000
        });
        this.liveAudioContext = audioContext;
        
        const source = audioContext.createMediaStreamSource(stream);
        this.liveSource = source;
        
        const processor = audioContext.createScriptProcessor(4096, 1, 1);
        this.liveProcessor = processor;
        
        // Create a silent gain node to avoid output to speakers
        const gainNode = audioContext.createGain();
        gainNode.gain.value = 0;
        
        processor.onaudioprocess = (event) => {
            const inputData = event.inputBuffer.getChannelData(0);
            const pcmData = new Int16Array(inputData.length);
            for (let i = 0; i < inputData.length; i++) {
                pcmData[i] = Math.max(-32768, Math.min(32767, inputData[i] * 32768));
            }
            if (this.asrSocket.readyState === WebSocket.OPEN) {
                this.asrSocket.send(pcmData.buffer);
            }
        };
        
        source.connect(processor);
        processor.connect(gainNode);
        gainNode.connect(audioContext.destination);
    } catch (error) {
        console.error('[PCM Streaming] Failed to start:', error);
    }
};

UniversalAudioHandler.prototype._stopPcmStreaming = function() {
    if (this.liveProcessor) {
        this.liveProcessor.disconnect();
        this.liveProcessor = null;
    }
    if (this.liveSource) {
        this.liveSource.disconnect();
        this.liveSource = null;
    }
    if (this.liveAudioContext) {
        this.liveAudioContext.close();
        this.liveAudioContext = null;
    }
};

UniversalAudioHandler.prototype._reacquireWakeLock = async function() {
    if (document.visibilityState === 'visible' && this.shouldKeepListening) {
        try { this.wakeLock = await navigator.wakeLock.request('screen'); } catch (e) { /* ignore */ }
    }
};
