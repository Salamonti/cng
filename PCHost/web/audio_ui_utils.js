(function () {
  function setRecordingButtonsState(isRecording) {
    const ids = ['recordBtnSidebar', 'recordBtnInline', 'recordBtnInlineRound'];
    ids.forEach((id) => {
      const btn = document.getElementById(id);
      if (!btn) return;

      // Toggle visual state
      if (isRecording) {
        btn.classList.add('recording-active');
        btn.classList.remove('recording-ready');
      } else {
        btn.classList.remove('recording-active');
        btn.classList.add('recording-ready');
      }

      // Update label text: "Record" when idle, "Stop" when recording.
      // Prefer a dedicated span.rec-label; otherwise attempt a safe fallback.
      const labelEl = btn.querySelector && btn.querySelector('.rec-label');
      if (labelEl) {
        labelEl.textContent = isRecording ? 'Stop' : 'Record';
      } else {
        // Fallback: only touch text for known patterns to avoid clobbering icon-only buttons.
        const t = (btn.textContent || '').trim();
        if (t.includes('Record') || t.includes('Stop') || t.includes('Record / Stop') || t.includes('Record/Stop')) {
          btn.textContent = isRecording ? 'Stop' : 'Record';
        }
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
