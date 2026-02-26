(function () {
  function focusCurrentSection() {
    const authCard = document.getElementById('authCard');
    const scrollTarget = authCard || document.getElementById('chartCard');
    if (!scrollTarget) return;

    try {
      scrollTarget.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch {}

    const focusTarget = document.getElementById('logoutBtn') || document.getElementById('authStatus');
    if (focusTarget) {
      try { focusTarget.focus({ preventScroll: true }); } catch { try { focusTarget.focus(); } catch {} }
    }
  }

  function goToGeneratedNoteCardActions() {
    const card = document.getElementById('noteCard');
    if (!card) return;

    try {
      card.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch {}

    const actionBtn = card.querySelector('.note-header-actions .btn.btn-success');
    if (actionBtn) {
      try { actionBtn.focus({ preventScroll: true }); } catch { try { actionBtn.focus(); } catch {} }
    }
  }

  function isAudioCaptureActive() {
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

  function clearPendingGenerateAfterTranscription() {
    const app = window.app;
    if (!app) return;
    app.pendingGenerateAfterTranscription = false;
    if (app.pendingGenerateTimer) {
      clearTimeout(app.pendingGenerateTimer);
      app.pendingGenerateTimer = null;
    }
  }

  function triggerGenerateNoteAndFocus() {
    goToGeneratedNoteCardActions();

    const app = window.app;
    if (!app) return;

    if (isAudioCaptureActive()) {
      clearPendingGenerateAfterTranscription();
      app.pendingGenerateAfterTranscription = true;
      if (typeof window.showToast === 'function') {
        window.showToast('Recording', 'Stopping recording and transcribing before generating note...', 'info');
      }

      app.pendingGenerateTimer = setTimeout(() => {
        if (!app.pendingGenerateAfterTranscription) return;
        clearPendingGenerateAfterTranscription();
        if (typeof window.showToast === 'function') {
          window.showToast('Audio Timeout', 'Transcription did not complete in time. Please stop recording and try Generate again.', 'warning');
        }
      }, 45000);

      try {
        if (typeof window.setRecordingButtonsState === 'function') {
          window.setRecordingButtonsState(false);
        }
        if (window.universalAudio && typeof window.universalAudio.stopAudioRecording === 'function') {
          window.universalAudio.stopAudioRecording();
        } else if (typeof window.toggleSpeechRecognition === 'function') {
          window.toggleSpeechRecognition();
        }
      } catch (e) {
        clearPendingGenerateAfterTranscription();
        if (typeof window.showToast === 'function') {
          window.showToast('Audio Error', 'Could not stop recording cleanly. Please stop recording and try again.', 'error');
        }
      }
      return;
    }

    if (typeof window.generateNote === 'function') {
      window.generateNote();
    }
  }

  window.CNGGenerateUI = {
    focusCurrentSection,
    goToGeneratedNoteCardActions,
    isAudioCaptureActive,
    clearPendingGenerateAfterTranscription,
    triggerGenerateNoteAndFocus,
  };
})();
