(function () {
  function ensureRecLabel(btn) {
    if (!btn || !btn.querySelector) return null;
    let labelEl = btn.querySelector('.rec-label');
    if (labelEl) return labelEl;

    // If the button has any visible text (beyond icon entities), create a label span
    // so iOS/Safari text updates are reliable.
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

      // Update label text: "Record" when idle, "Stop" when recording.
      // Prefer a dedicated span.rec-label; otherwise create one.
      const labelEl = ensureRecLabel(btn);
      if (labelEl) {
        labelEl.textContent = isRecording ? 'Stop' : 'Record';
      }
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
