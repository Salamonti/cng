(function (global) {
  const DB_NAME = 'cng_recording_recovery_v1';
  const DB_VERSION = 1;
  const STORE_SESSIONS = 'sessions';
  /** IndexedDB object store name (blob segments from MediaRecorder; not ASR batching). */
  const STORE_BLOB_PARTS = 'chunks';
  const TTL_MS = 7 * 24 * 60 * 60 * 1000; // legal max retention: 7 days

  function nowMs() { return Date.now(); }

  function safeUserKey() {
    try {
      const u = window.AuthWorkspace && window.AuthWorkspace.user;
      const id = u && (u.id || u.user_id || u.sub);
      const email = u && u.email;
      return String(id || email || '');
    } catch (_) {
      return '';
    }
  }

  function safeEncounterId() {
    try {
      return window.app && window.app.activeEncounterId ? String(window.app.activeEncounterId) : '';
    } catch (_) {
      return '';
    }
  }

  function openDb() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(STORE_SESSIONS)) {
          const st = db.createObjectStore(STORE_SESSIONS, { keyPath: 'recordingId' });
          st.createIndex('by_scope', ['userKey', 'encounterId', 'createdAt'], { unique: false });
          st.createIndex('by_exp', 'expiresAt', { unique: false });
          st.createIndex('by_status', ['userKey', 'encounterId', 'status'], { unique: false });
        }
        if (!db.objectStoreNames.contains(STORE_BLOB_PARTS)) {
          const st2 = db.createObjectStore(STORE_BLOB_PARTS, { keyPath: ['recordingId', 'seq'] });
          st2.createIndex('by_rec', 'recordingId', { unique: false });
          st2.createIndex('by_exp', 'expiresAt', { unique: false });
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error || new Error('IndexedDB open failed'));
    });
  }

  async function withTx(stores, mode, fn) {
    const db = await openDb();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(stores, mode);
      const s = stores.map((n) => tx.objectStore(n));
      let out;

      tx.oncomplete = () => resolve(out);
      tx.onerror = () => reject(tx.error || new Error('IndexedDB tx failed'));
      tx.onabort = () => reject(tx.error || new Error('IndexedDB tx aborted'));

      try {
        const result = fn(tx, ...s);
        if (result && typeof result.then === 'function') {
          // Async callback — wait for it. The transaction stays alive because
          // IndexedDB keeps it open while there are pending requests.
          // tx.oncomplete will resolve(out) once all pending requests finish.
          result.then((v) => {
            out = v;
          }).catch((e) => {
            try { tx.abort(); } catch (_) {}
            reject(e);
          });
        } else {
          out = result;
        }
      } catch (e) {
        try { tx.abort(); } catch (_) {}
        reject(e);
      }
    });
  }

  function newId() {
    try {
      return crypto && crypto.randomUUID ? crypto.randomUUID() : `rec_${nowMs()}_${Math.random().toString(16).slice(2)}`;
    } catch (_) {
      return `rec_${nowMs()}_${Math.random().toString(16).slice(2)}`;
    }
  }

  async function cleanupExpired() {
    const t = nowMs();
    await withTx([STORE_SESSIONS, STORE_BLOB_PARTS], 'readwrite', (tx, sessions, chunks) => {
      return new Promise((resolve) => {
        // Sessions by expiresAt
        const idxS = sessions.index('by_exp');
        const reqS = idxS.openCursor(IDBKeyRange.upperBound(t));
        reqS.onsuccess = () => {
          const cur = reqS.result;
          if (!cur) {
            // Also clean orphan chunks
            const idxC2 = chunks.index('by_exp');
            const reqC2 = idxC2.openCursor(IDBKeyRange.upperBound(t));
            reqC2.onsuccess = () => {
              const cur2 = reqC2.result;
              if (!cur2) return resolve();
              cur2.delete();
              cur2.continue();
            };
            reqC2.onerror = () => resolve();
            return;
          }
          const recId = cur.primaryKey;
          cur.delete();
          // delete chunks for this recording
          const idxC = chunks.index('by_rec');
          const reqC = idxC.openCursor(IDBKeyRange.only(recId));
          reqC.onsuccess = () => {
            const c2 = reqC.result;
            if (!c2) { cur.continue(); return; }
            c2.delete();
            c2.continue();
          };
          reqC.onerror = () => { cur.continue(); };
        };
        reqS.onerror = () => resolve();
      });
    });
  }

  async function startRecordingSession({ mimeType, fileName, encounterId, userKey, baselineText } = {}) {
    const recordingId = newId();
    const u = String(userKey || safeUserKey() || '');
    const e = String(encounterId || safeEncounterId() || '');
    const createdAt = nowMs();
    const expiresAt = createdAt + TTL_MS;
    if (!u || !e) {
      // Without scope we refuse to persist for compliance reasons.
      return { ok: false, recordingId: null, reason: 'missing_scope' };
    }
    await withTx([STORE_SESSIONS], 'readwrite', (_tx, sessions) => {
      sessions.put({
        recordingId,
        userKey: u,
        encounterId: e,
        createdAt,
        expiresAt,
        mimeType: String(mimeType || 'audio/webm'),
        fileName: String(fileName || `recording_${createdAt}.webm`),
        baselineText: typeof baselineText === 'string' ? baselineText : '',
        status: 'recording',
        uploadedAt: null,
        stoppedAt: null,
      });
    });
    return { ok: true, recordingId, expiresAt };
  }

  async function appendChunk(recordingId, seq, blob) {
    if (!recordingId || !blob) return false;
    const t = nowMs();
    // Resolve true ONLY from the put request's own onsuccess -- resolving
    // from sessions.get()'s onsuccess (the old behavior) reported success
    // before the chunk write had even been submitted, let alone completed.
    // A failed session lookup or a failed put is now a real error (rejected
    // and converted to false below), not silently swallowed as success.
    const ok = await withTx([STORE_SESSIONS, STORE_BLOB_PARTS], 'readwrite', (_tx, sessions, chunks) => {
      return new Promise((resolve, reject) => {
        const req = sessions.get(recordingId);
        req.onsuccess = () => {
          const row = req.result;
          if (!row) { resolve(false); return; }
          const putReq = chunks.put({
            recordingId,
            seq: Number(seq) || 0,
            blob,
            createdAt: t,
            expiresAt: row.expiresAt || (t + TTL_MS),
          });
          putReq.onsuccess = () => resolve(true);
          putReq.onerror = () => reject(putReq.error || new Error('chunk put failed'));
        };
        req.onerror = () => reject(req.error || new Error('session lookup failed'));
      });
    }).catch(() => false);
    return ok === true;
  }

  async function finalizeRecording(recordingId) {
    if (!recordingId) return false;
    const t = nowMs();
    await withTx([STORE_SESSIONS], 'readwrite', (_tx, sessions) => {
      return new Promise((resolve) => {
        const req = sessions.get(recordingId);
        req.onsuccess = () => {
          const row = req.result;
          if (!row) return resolve();
          row.status = 'stopped';
          row.stoppedAt = t;
          sessions.put(row);
        };
        req.onerror = () => resolve();
      });
    });
    return true;
  }

  async function assembleFile(recordingId) {
    if (!recordingId) return null;
    const result = await withTx([STORE_SESSIONS, STORE_BLOB_PARTS], 'readonly', (_tx, sessions, chunks) => {
      return new Promise((resolve) => {
        const reqS = sessions.get(recordingId);
        reqS.onsuccess = () => {
          const sess = reqS.result;
          if (!sess) return resolve(null);
          const parts = [];
          const idx = chunks.index('by_rec');
          const reqC = idx.openCursor(IDBKeyRange.only(recordingId));
          reqC.onsuccess = () => {
            const cur = reqC.result;
            if (!cur) {
              const blob = new Blob(parts, { type: sess.mimeType || 'audio/webm' });
              const file = new File([blob], sess.fileName || `recording_${sess.createdAt || nowMs()}.webm`, { type: blob.type });
              return resolve({ file, sess });
            }
            parts.push(cur.value.blob);
            cur.continue();
          };
        };
        reqS.onerror = () => resolve(null);
      });
    });
    return result;
  }

  async function markUploadedAndDelete(recordingId) {
    if (!recordingId) return;
    const t = nowMs();
    await withTx([STORE_SESSIONS, STORE_BLOB_PARTS], 'readwrite', (_tx, sessions, chunks) => {
      return new Promise((resolve) => {
        const req = sessions.get(recordingId);
        req.onsuccess = () => {
          const row = req.result;
          if (row) {
            row.status = 'uploaded';
            row.uploadedAt = t;
            sessions.put(row);
          }
          const idx = chunks.index('by_rec');
          const reqC = idx.openCursor(IDBKeyRange.only(recordingId));
          reqC.onsuccess = () => {
            const cur = reqC.result;
            if (!cur) return resolve();
            cur.delete();
            cur.continue();
          };
          reqC.onerror = () => resolve();
        };
        req.onerror = () => resolve();
      });
    });
  }

  /** Collect recording ids for a given user+encounter and status. */
  function _collectIdsByStatus(sessions, u, e, status) {
    return new Promise((resolve) => {
      const out = [];
      const idx = sessions.index('by_status');
      const req = idx.openCursor(IDBKeyRange.only([u, e, status]));
      req.onsuccess = () => {
        const cur = req.result;
        if (!cur) return resolve(out);
        out.push(cur.primaryKey);
        cur.continue();
      };
    });
  }

  async function recoverStoppedToServer({ userKey, encounterId, uploadRecording, includeRecording = true, excludeRecordingIds = [] }) {
    const u = String(userKey || safeUserKey() || '');
    const e = String(encounterId || safeEncounterId() || '');
    if (!u || !e || typeof uploadRecording !== 'function') return { recovered: 0 };
    await cleanupExpired();
    const exclude = new Set((excludeRecordingIds || []).map((x) => String(x || '')).filter(Boolean));

    // Recover both 'stopped' (normal stop but upload failed) and 'recording'
    // (tab crashed mid-recording). With the 5 s timeslice, 'recording' sessions
    // may have partial chunks that can still be transcribed.
    const ids = await withTx([STORE_SESSIONS], 'readonly', (_tx, sessions) =>
      Promise.all([
        includeRecording ? _collectIdsByStatus(sessions, u, e, 'recording') : Promise.resolve([]),
        _collectIdsByStatus(sessions, u, e, 'stopped'),
      ]).then(([a, b]) => a.concat(b).filter((rid) => !exclude.has(String(rid || ''))))
    );

    let recovered = 0;
    for (const rid of ids) {
      const assembled = await assembleFile(rid);
      if (!assembled || !assembled.file) continue;
      try {
        const res = await uploadRecording({
          file: assembled.file,
          fileName: assembled.file.name,
          recordingSessionId: rid,
          baselineText: typeof assembled.sess?.baselineText === 'string' ? assembled.sess.baselineText : '',
        });
        // uploadRecording resolves with { ok: false } on failure instead of
        // throwing, so the catch below does NOT cover a failed upload. Only
        // delete the recovery copy only when the retained server recording exists.
        if (res && res.ok && res.mode === 'server') {
          await markUploadedAndDelete(rid);
          recovered += 1;
        }
      } catch (_) {
        // Leave it so it can be retried later (within TTL)
      }
    }
    return { recovered };
  }

  /** Remove one recovery session and its chunks after live Record→Stop handled. */
  async function deleteRecording(recordingId) {
    if (!recordingId) return false;
    await withTx([STORE_SESSIONS, STORE_BLOB_PARTS], 'readwrite', (_tx, sessions, chunks) => {
      return new Promise((resolve) => {
        sessions.delete(recordingId);
        const idx = chunks.index('by_rec');
        const reqC = idx.openCursor(IDBKeyRange.only(recordingId));
        reqC.onsuccess = () => {
          const cur = reqC.result;
          if (!cur) return resolve();
          cur.delete();
          cur.continue();
        };
        reqC.onerror = () => resolve();
      });
    });
    return true;
  }

  async function deleteByEncounter({ userKey, encounterId } = {}) {
    const u = String(userKey || safeUserKey() || '');
    const e = String(encounterId || safeEncounterId() || '');
    if (!u || !e) return { deleted: 0 };
    const ids = await withTx([STORE_SESSIONS], 'readonly', (_tx, sessions) => {
      return new Promise((resolve) => {
        const out = [];
        const idx = sessions.index('by_scope');
        // by_scope = [userKey, encounterId, createdAt]
        const range = IDBKeyRange.bound([u, e, 0], [u, e, Number.MAX_SAFE_INTEGER]);
        const req = idx.openCursor(range);
        req.onsuccess = () => {
          const cur = req.result;
          if (!cur) return resolve(out);
          out.push(cur.primaryKey);
          cur.continue();
        };
      });
    });

    let deleted = 0;
    for (const rid of ids) {
      try {
        await deleteRecording(rid);
        deleted += 1;
      } catch (_) {}
    }
    return { deleted };
  }

  /** Mark a recording as 'stopped' so server recovery can pick it up later. */
  async function markStopped(recordingId) {
    if (!recordingId) return false;
    return withTx([STORE_SESSIONS], 'readwrite', (_tx, sessions) => {
      return new Promise((resolve) => {
        const req = sessions.get(recordingId);
        req.onsuccess = () => {
          const row = req.result;
          if (row) {
            row.status = 'stopped';
            const putReq = sessions.put(row);
            putReq.onsuccess = () => resolve(true);
            putReq.onerror = () => resolve(false);
          } else {
            resolve(false);
          }
        };
        req.onerror = () => resolve(false);
      });
    });
  }

  const api = {
    TTL_MS,
    cleanupExpired,
    safeUserKey,
    safeEncounterId,
    startRecordingSession,
    appendChunk,
    finalizeRecording,
    recoverStoppedToServer,
    markUploadedAndDelete,
    markStopped,
    deleteRecording,
    deleteByEncounter,
  };
  try {
    global.RecordingRecovery = api;
  } catch (e) {}
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
})(typeof globalThis !== 'undefined' ? globalThis : this);

