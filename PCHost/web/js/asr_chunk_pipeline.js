/**
 * Whisper chunk pipeline — upload each ~25s segment, transcribe serially, update UI.
 *
 * Pattern: fixed-duration segment capture + ordered transcription queue (industry
 * standard for pseudo-streaming ASR when the model is batch-only).
 */
(function (global) {
  var SEGMENT_MS = 25000;

  function AsrChunkPipeline(opts) {
    opts = opts || {};
    this._uploadFn = opts.uploadFn;
    this._transcribeFn = opts.transcribeFn;
    this._fetchMergedFn = opts.fetchMergedFn;
    this._drainFn = opts.drainFn;
    this._onTranscript = opts.onTranscript || function () {};
    this._onStatus = opts.onStatus || function () {};
    this._sessionId = '';
    this._encounterId = '';
    this._chunkIndex = 0;
    this._queue = [];
    this._workerPromise = null;
    this._idleWaiters = [];
    this._uploadsInFlight = 0;
    this._startedAt = '';
    this._lastMerged = '';
  }

  AsrChunkPipeline.prototype.start = function (ctx) {
    ctx = ctx || {};
    this._encounterId = String(ctx.encounterId || '');
    this._sessionId = String(ctx.sessionId || ('rec_' + Date.now() + '_' + Math.random().toString(16).slice(2)));
    this._chunkIndex = 0;
    this._queue = [];
    this._workerPromise = null;
    this._uploadsInFlight = 0;
    this._startedAt = new Date().toISOString();
    this._lastMerged = '';
  };

  AsrChunkPipeline.prototype.getSessionId = function () {
    return this._sessionId;
  };

  AsrChunkPipeline.prototype._notifyIdleWaiters = function () {
    if (this._workerPromise || this._queue.length > 0 || this._uploadsInFlight > 0) {
      return;
    }
    var waiters = this._idleWaiters.splice(0);
    waiters.forEach(function (r) { try { r(); } catch (_) {
      // A waiter's resolve callback must never abort the idle sweep of the
      // remaining waiters.
    } });
  };

  AsrChunkPipeline.prototype.submitSegment = function (blob, opts) {
    opts = opts || {};
    var self = this;
    if (!blob || blob.size < 1000 || !this._uploadFn) {
      return Promise.resolve(null);
    }
    var idx = this._chunkIndex;
    this._chunkIndex += 1;
    var role = opts.isFinal ? 'partial_tail' : 'chunk';
    var clientId = this._sessionId + '-c' + String(idx).padStart(3, '0');
    var file = blob instanceof File
      ? blob
      : new File([blob], clientId + '.webm', { type: blob.type || 'audio/webm' });

    this._uploadsInFlight += 1;
    return Promise.resolve(this._uploadFn(file, {
      encounterId: this._encounterId,
      clientRecordingId: clientId,
      recordingSessionId: this._sessionId,
      segmentRole: role,
      chunkIndex: idx,
      startedAt: opts.startedAt || this._startedAt,
      stoppedAt: opts.stoppedAt || '',
      durationSec: opts.durationSec,
    })).then(function (upload) {
      if (upload && upload.ok && upload.segmentId) {
        self._enqueueTranscribe(String(upload.segmentId), idx);
      }
      return upload;
    }).finally(function () {
      self._uploadsInFlight = Math.max(0, self._uploadsInFlight - 1);
      self._notifyIdleWaiters();
    });
  };

  AsrChunkPipeline.prototype._enqueueTranscribe = function (segmentId, chunkIndex) {
    this._queue.push({ segmentId: segmentId, chunkIndex: chunkIndex });
    this._queue.sort(function (a, b) { return a.chunkIndex - b.chunkIndex; });
    this._ensureWorker();
  };

  AsrChunkPipeline.prototype._ensureWorker = function () {
    if (this._workerPromise) return;
    var self = this;
    this._workerPromise = (async function () {
      while (self._queue.length > 0) {
        var item = self._queue.shift();
        self._onStatus('transcribing', 'Transcribing segment ' + (item.chunkIndex + 1) + '…');
        try {
          var result = await self._transcribeFn(item.segmentId);
          if (result && result.ok === false) {
            throw new Error(result.message || 'Transcription failed');
          }
          var merged = await self._fetchMergedFn();
          if (merged) {
            self._onTranscript(merged, { chunkIndex: item.chunkIndex, segmentId: item.segmentId });
          }
          self._onStatus('recording', 'Segment ' + (item.chunkIndex + 1) + ' ready — still recording…');
        } catch (e) {
          console.warn('[AsrChunkPipeline] transcribe failed', e);
          self._onStatus('error', 'Segment ' + (item.chunkIndex + 1) + ' transcription failed');
        }
      }
      self._workerPromise = null;
      self._notifyIdleWaiters();
    })();
  };

  AsrChunkPipeline.prototype.waitUntilIdle = function () {
    var self = this;
    if (!this._workerPromise && this._queue.length === 0 && this._uploadsInFlight === 0) {
      return Promise.resolve();
    }
    return new Promise(function (resolve) {
      self._idleWaiters.push(resolve);
    });
  };

  AsrChunkPipeline.prototype.ensureSessionComplete = async function () {
    await this.waitUntilIdle();
    if (this._drainFn) {
      try {
        var pipeline = await this._drainFn(this._encounterId, this._sessionId);
        if (pipeline && pipeline.merged_transcript_text) {
          this._lastMerged = String(pipeline.merged_transcript_text).trim();
          if (this._lastMerged) {
            this._onTranscript(this._lastMerged, { final: true });
          }
        }
      } catch (e) {
        console.warn('[AsrChunkPipeline] server drain failed', e);
      }
    }
    await this.waitUntilIdle();
  };

  AsrChunkPipeline.prototype.fetchMerged = function () {
    if (this._lastMerged) {
      return Promise.resolve(this._lastMerged);
    }
    if (!this._fetchMergedFn) return Promise.resolve('');
    return Promise.resolve(this._fetchMergedFn());
  };

  global.AsrChunkPipeline = AsrChunkPipeline;
  global.ASR_CHUNK_SEGMENT_MS = SEGMENT_MS;

})(typeof window !== 'undefined' ? window : globalThis);
