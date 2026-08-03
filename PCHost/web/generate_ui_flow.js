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
    const header = document.getElementById('generatedNoteCardHeader');
    const card = document.getElementById('noteCard');
    const scrollTarget = header || card;
    if (!scrollTarget) return;

    // Align viewport with the "Generated note / Review and edit" header — not the card center
    // (block:center pushed the title to an awkward vertical position).
    try {
      scrollTarget.scrollIntoView({ behavior: 'smooth', block: 'start', inline: 'nearest' });
    } catch {}

    if (header) {
      try {
        header.focus({ preventScroll: true });
      } catch {
        try {
          header.focus();
        } catch {}
      }
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

  /** Mic inactive but ASR/note pipeline still busy (e.g. after Record→Stop). */
  function isPipelineBusyWithoutCapture(app) {
    if (!app?.audioPipelinePhase) return false;
    const ph = app.audioPipelinePhase;
    return ph === 'stopping' || ph === 'transcribing' || ph === 'generating_note';
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

  async function triggerGenerateNoteAndFocus() {
    goToGeneratedNoteCardActions();

    const app = window.app;
    if (!app) return;

    /* Generate-while-recording already queued this encounter — avoid a second Generate hitting generateNote() early. */
    if (app.pendingGenerateAfterTranscription && !isAudioCaptureActive()) {
      if (typeof window.showToast === 'function') {
        window.showToast('Please wait', 'Finishing recording and transcription for your note…', 'info');
      }
      return;
    }

    /* Don't start a fresh Generate while a prior stop/transcribe/generate is running — unless this click is chaining Generate-after-record (pending flag set below). */
    if (isPipelineBusyWithoutCapture(app) && !isAudioCaptureActive() && !app.pendingGenerateAfterTranscription) {
      if (typeof window.showToast === 'function') {
        window.showToast('Please wait', 'Transcription or note generation is still finishing.', 'info');
      }
      return;
    }

    if (isAudioCaptureActive()) {
      if (typeof window.showToast === 'function') {
        window.showToast(
          'Recording in progress',
          'Stop recording first, then generate your note.',
          'info'
        );
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
