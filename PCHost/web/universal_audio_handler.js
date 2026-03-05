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
        
        this.onAsrStatusCallback = null;
        this.liveStream = null;
        this._chunkWindow = [];
        this._chunkTick = 0;
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

    // Start speech recognition - chunked HTTP progressive mode (no WebSocket)
    async startSpeechRecognition() {
        console.log('[AudioHandler] Using chunked HTTP ASR mode');

        try {
            this.shouldKeepListening = true;
            this.isListening = true;
            this.dictationChunks = [];
            this._chunkWindow = [];
            this._chunkTick = 0;

            try { if ('wakeLock' in navigator) { this._requestWakeLock && this._requestWakeLock(); } } catch (e) {}
            this._asrStatus('chunking', 'Live chunk transcription active');

            const stream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    echoCancellation: true,
                    noiseSuppression: true,
                    sampleRate: 16000
                }
            });
            this.liveStream = stream;

            let options = { mimeType: 'audio/webm' };
            if (!MediaRecorder.isTypeSupported('audio/webm')) {
                if (MediaRecorder.isTypeSupported('audio/wav')) options = { mimeType: 'audio/wav' };
                else options = {};
            }

            this.dictationRecorder = new MediaRecorder(stream, options);
            const CHUNK_SLICE_MS = 2000;      // recorder cadence
            const CHUNK_WINDOW_PARTS = 5;     // 10s total
            const CHUNK_STEP_PARTS = 4;       // 8s step => 2s overlap

            this.dictationRecorder.ondataavailable = async (event) => {
                if (!event || event.data.size <= 0) return;
                this._chunkWindow.push(event.data);
                if (this._chunkWindow.length > CHUNK_WINDOW_PARTS) this._chunkWindow.shift();
                this._chunkTick += 1;

                // Upload every 8s once we have enough context (10s rolling window)
                if (this._chunkWindow.length === CHUNK_WINDOW_PARTS && (this._chunkTick % CHUNK_STEP_PARTS === 0)) {
                    const outType = this.dictationRecorder.mimeType || event.data.type || 'audio/webm';
                    const ext = this._extensionFromMime(outType);
                    const blob = new Blob(this._chunkWindow, { type: outType });
                    const file = new File([blob], `dictation_chunk_${Date.now()}.${ext}`, { type: outType });
                    if (this.onAudioFileCallback) this.onAudioFileCallback(file, { chunkMode: true });
                }
            };

            this.dictationRecorder.start(CHUNK_SLICE_MS);
            this.onSpeechStart();
        } catch (error) {
            console.error('Failed to start chunked speech recognition:', error);
            this.isListening = false;
            throw error;
        }
    }

    // Stop speech recognition
    async stopSpeechRecognition() {
        this.shouldKeepListening = false;
        this.isListening = false;

        if (this.dictationRecorder && this.dictationRecorder.state !== 'inactive') {
            this.dictationRecorder.stop();
            await new Promise(resolve => {
                this.dictationRecorder.onstop = async () => {
                    // Final tail flush only (no full final pass).
                    // Use the most recent media chunk to avoid invalid container artifacts
                    // from concatenating partially finalized webm segments.
                    if (this._chunkWindow && this._chunkWindow.length > 0) {
                        const lastPart = this._chunkWindow[this._chunkWindow.length - 1];
                        const outType = this.dictationRecorder.mimeType || lastPart?.type || 'audio/webm';
                        const ext = this._extensionFromMime(outType);
                        const tailFile = new File([lastPart], `dictation_tail_${Date.now()}.${ext}`, { type: outType });
                        if (this.onAudioFileCallback) this.onAudioFileCallback(tailFile, { chunkMode: true, finalTail: true });
                    }

                    if (this.dictationRecorder && this.dictationRecorder.stream) {
                        this.dictationRecorder.stream.getTracks().forEach(track => track.stop());
                    }
                    this.dictationChunks = [];
                    this._chunkWindow = [];
                    this._chunkTick = 0;
                    resolve();
                };
            });
        }

        this._asrStatus('idle', 'Stopped');
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
                    this.audioChunks.push(event.data);
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

UniversalAudioHandler.prototype._reacquireWakeLock = async function() {
    if (document.visibilityState === 'visible' && this.shouldKeepListening) {
        try { this.wakeLock = await navigator.wakeLock.request('screen'); } catch (e) { /* ignore */ }
    }
};
