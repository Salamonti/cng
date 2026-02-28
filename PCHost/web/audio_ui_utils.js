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

  function setRecordingButtonsState(isRecording) {
    const ids = ['recordBtnSidebar', 'recordBtnInline', 'recordBtnInlineRound'];

    // Update explicit IDs + any other record buttons that may exist in mobile/desktop variants
    const btns = new Set();
    ids.forEach((id) => {
      const b = document.getElementById(id);
      if (b) btns.add(b);
    });
    document.querySelectorAll('button.record-toggle, button.record-round-btn, button[onclick*="toggleSpeechRecognition"]').forEach((b) => btns.add(b));

    btns.forEach((btn) => {
      // Toggle visual state
      if (isRecording) {
        btn.classList.add('recording-active');
        btn.classList.remove('recording-ready');
      } else {
        btn.classList.remove('recording-active');
        btn.classList.add('recording-ready');
      }

      // Icon-only round button: never show text
      if (isIconOnlyRecordButton(btn)) {
        const existing = btn.querySelector && btn.querySelector('.rec-label');
        if (existing) existing.remove();
        return;
      }

      // Update label text: "Record" when idle, "Stop" when recording.
      const labelEl = ensureRecLabel(btn);
      if (labelEl) labelEl.textContent = isRecording ? 'Stop' : 'Record';
    });
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
    setRecordingButtonsState,
    debugAudioCapabilities,
  };
})();
