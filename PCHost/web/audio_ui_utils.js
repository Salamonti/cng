(function () {
  function isIconOnlyRecordButton(btn) {
    return !!(btn && (btn.id === 'recordBtnInlineRound' || btn.classList?.contains('record-round-btn')));
  }

  function ensureRecLabel(btn) {
    if (!btn || !btn.querySelector) return null;

    // Round chart-header record button should remain icon-only.
    if (isIconOnlyRecordButton(btn)) {
      const existing = btn.querySelector('.rec-label');
      if (existing) existing.remove();
      return null;
    }

    let labelEl = btn.querySelector('.rec-label');
    if (labelEl) return labelEl;

    // Create a label span so iOS/Safari text updates are reliable.
    labelEl = document.createElement('span');
    labelEl.className = 'rec-label';
    labelEl.style.marginLeft = '6px';
    btn.appendChild(labelEl);
    return labelEl;
  }

  /** @typedef {'idle'|'recording'|'stopping'|'transcribing'|'generating_note'} RecordingPipelinePhase */

  /**
   * Primary UI driver for Record / Stop controls: phase matches capture + transcribe pipeline.
   */
  function syncRecordingPipelineUi(phase) {
    const ids = ['recordBtnSidebar', 'recordBtnInline', 'recordBtnInlineRound'];
    const btns = new Set();
    ids.forEach((id) => {
      const b = document.getElementById(id);
      if (b) btns.add(b);
    });
    document.querySelectorAll('button.record-toggle, button.record-round-btn, button[onclick*="toggleSpeechRecognition"]').forEach((b) => btns.add(b));

    const uiPhase = phase === 'stopping' ? 'transcribing' : phase;
    const labels = {
      idle: 'Record',
      recording: 'Stop',
      transcribing: 'Transcribing…',
      generating_note: 'Generating…',
    };
    const labelText = labels[uiPhase] || labels.idle;
    const busy = phase === 'stopping' || phase === 'transcribing' || phase === 'generating_note';

    btns.forEach((btn) => {
      btn.classList.remove('recording-active', 'recording-ready', 'recording-pipeline-wait');
      if (phase === 'recording') {
        btn.classList.add('recording-active');
        btn.disabled = false;
      } else if (phase === 'idle') {
        btn.classList.add('recording-ready');
        btn.disabled = false;
      } else if (busy) {
        btn.classList.add('recording-pipeline-wait');
        btn.disabled = true;
      } else {
        btn.classList.add('recording-ready');
        btn.disabled = false;
      }

      btn.setAttribute('aria-busy', busy ? 'true' : 'false');

      if (isIconOnlyRecordButton(btn)) {
        const existing = btn.querySelector && btn.querySelector('.rec-label');
        if (existing) existing.remove();
        btn.title = labelText;
        return;
      }

      const labelEl = ensureRecLabel(btn);
      if (labelEl) labelEl.textContent = labelText;
    });

    syncGenerateButtonsForPipeline(phase);
  }

  function isLiveAudioCaptureActive() {
    const audio = window.universalAudio;
    if (!audio) return false;
    return !!(
      audio.isRecording ||
      audio.isListening ||
      audio.shouldKeepListening ||
      audio.shouldKeepRecording ||
      (audio.mediaRecorder && audio.mediaRecorder.state === 'recording') ||
      (audio.dictationRecorder && audio.dictationRecorder.state === 'recording')
    );
  }

  // Note types allowed to generate WHILE recording (document-oriented: consume
  // prior visits/chart, not the live transcript). Keep in sync with
  // DOCUMENT_ORIENTED_NOTE_TYPES in server/core/prompt/builder.py (server is truth)
  // and MID_RECORDING_ALLOWED_TYPES in workspace_app.js generateNote().
  const MID_RECORDING_ALLOWED_TYPES = ['pre_encounter_prep'];

  function currentNoteTypeValue() {
    const el = document.getElementById('noteType');
    return el ? String(el.value || '') : '';
  }

  function isMidRecordingGenerateAllowed() {
    return MID_RECORDING_ALLOWED_TYPES.indexOf(currentNoteTypeValue()) >= 0;
  }

  function syncGenerateButtonsForPipeline(phase) {
    const recording = (phase === 'recording' || isLiveAudioCaptureActive())
      && !isMidRecordingGenerateAllowed();
    const busy = phase === 'stopping' || phase === 'transcribing' || phase === 'generating_note';
    const selectors = [
      'button[onclick*="triggerGenerateNoteAndFocus"]',
      '#generateBtnHeader',
      '#generateBtnNoteCard',
    ];
    const seen = new Set();
    selectors.forEach((sel) => {
      document.querySelectorAll(sel).forEach((btn) => {
        if (!btn || seen.has(btn)) return;
        seen.add(btn);
        if (recording) {
          btn.disabled = true;
          btn.setAttribute('aria-disabled', 'true');
          btn.title = 'Stop recording first, then generate your note.';
          btn.setAttribute('aria-label', 'Generate (disabled while recording)');
          btn.classList.add('generate-blocked-recording');
        } else {
          btn.disabled = !!busy;
          btn.setAttribute('aria-disabled', busy ? 'true' : 'false');
          btn.title = busy
            ? 'Please wait — transcription or note generation is still finishing.'
            : '';
          btn.removeAttribute('aria-label');
          btn.classList.remove('generate-blocked-recording');
        }
      });
    });
  }

  /** @deprecated Prefer syncRecordingPipelineUi — kept for narrow compatibility */
  function setRecordingButtonsState(isRecording) {
    syncRecordingPipelineUi(isRecording ? 'recording' : 'idle');
  }

  function debugAudioCapabilities() {
    const audioHandler = window.universalAudio;
    if (!audioHandler) {
      if (typeof window.showToast === 'function') {
        window.showToast('Audio', 'Audio handler not initialized', 'warning');
      }
      return;
    }
    const guidance = audioHandler.getBrowserGuidance();
    const msg = `Browser: ${audioHandler.capabilities?.isChromeiOS ? 'Chrome iOS' : 'Other'}\n`
      + `Speech Available: ${guidance.speechAvailable}\n`
      + `Recording Available: ${guidance.recordingAvailable}\n`
      + `Has MediaRecorder: ${!!window.MediaRecorder}\n`
      + `Has getUserMedia: ${!!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia)}`;

    if (typeof window.showToast === 'function') {
      window.showToast('Audio Debug', msg, 'info');
    }
    if (typeof window.debugLog === 'function') {
      window.debugLog('[Audio Debug]', msg);
    }
  }

  window.CNGAudioUI = {
    syncRecordingPipelineUi,
    setRecordingButtonsState,
    syncGenerateButtonsForPipeline,
    debugAudioCapabilities,
  };
})();
