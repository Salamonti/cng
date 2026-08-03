'use strict';

// Regression test (WO-3 C-2): recording_recovery.js's appendChunk() must
// report the REAL outcome of a chunk write, not a hardcoded true.
//
// Before this fix: the promise resolved from sessions.get()'s onsuccess
// callback (before chunks.put() had even been submitted, let alone
// completed), a failed session lookup silently resolved as success, and
// appendChunk() returned `true` unconditionally regardless of what
// actually happened inside the transaction. A storage-quota error on a
// long encounter was invisible until crash-recovery produced an empty
// file.
//
// Uses a minimal hand-rolled fake IndexedDB (no fake-indexeddb dependency
// in this project) scoped to exactly the subset of the API
// recording_recovery.js actually calls: db.transaction(), objectStore(),
// .get(), .put(), with realistic async request/transaction completion
// ordering (a transaction only completes once every request issued
// against it -- including ones queued from inside another request's own
// callback -- has settled).

const assert = require('assert');
const path = require('path');

// Minimal IDBKeyRange covering exactly what recording_recovery.js's own
// createIndex/openCursor calls use: .only() (equality, including compound
// array keys via JSON comparison) and .upperBound() (used by cleanupExpired(),
// which every recoverStoppedToServer() call runs first).
class FakeKeyRange {
    static only(value) { return { type: 'only', value }; }
    static upperBound(value) { return { type: 'upperBound', value }; }
}
global.IDBKeyRange = FakeKeyRange;

// Index definitions actually used by recording_recovery.js's cursor-based
// reads (see createIndex calls in openDb()): how to derive an index key from
// a stored row.
const INDEX_KEY_FNS = {
    sessions: {
        by_status: (row) => [row.userKey, row.encounterId, row.status],
        by_exp: (row) => row.expiresAt,
    },
    chunks: {
        by_rec: (row) => row.recordingId,
        by_exp: (row) => row.expiresAt,
    },
};

function keyMatches(indexKey, range) {
    if (!range) return true;
    if (range.type === 'only') return JSON.stringify(indexKey) === JSON.stringify(range.value);
    if (range.type === 'upperBound') return indexKey !== undefined && indexKey <= range.value;
    return true;
}

function makeFakeIndexedDB({ sessionRows = {}, chunkRows = {}, failPutInStore = null } = {}) {
    const stores = {
        sessions: new Map(Object.entries(sessionRows)),
        chunks: new Map(Object.entries(chunkRows)),
    };

    function keyFor(storeName, value) {
        return storeName === 'sessions' ? value.recordingId || value : `${value.recordingId}:${value.seq}`;
    }

    function makeObjectStore(storeName, tx) {
        function issue(fn) {
            tx._pending++;
            const req = { result: undefined, error: undefined, onsuccess: null, onerror: null };
            Promise.resolve().then(() => {
                let isError = false;
                let value;
                try {
                    value = fn();
                } catch (e) {
                    isError = true;
                    value = e;
                }
                if (isError) {
                    req.error = value;
                    if (typeof req.onerror === 'function') req.onerror({ target: req });
                } else {
                    req.result = value;
                    if (typeof req.onsuccess === 'function') req.onsuccess({ target: req });
                }
                tx._settle();
            });
            return req;
        }

        // openCursor() walks matching rows synchronously across ticks, firing
        // onsuccess once per row (result = cursor) and once more with
        // result = null at the end -- matching real IDBCursor semantics
        // closely enough for cur.continue()-driven iteration to work.
        function issueCursor(indexName, range) {
            const keyFn = INDEX_KEY_FNS[storeName] && INDEX_KEY_FNS[storeName][indexName];
            const rows = [...stores[storeName].entries()].filter(([, row]) =>
                keyMatches(keyFn ? keyFn(row) : undefined, range)
            );
            let i = 0;
            const req = { result: undefined, onsuccess: null, onerror: null };
            const emit = () => {
                tx._pending++;
                Promise.resolve().then(() => {
                    if (i >= rows.length) {
                        req.result = null;
                    } else {
                        const [primaryKey, value] = rows[i];
                        req.result = {
                            primaryKey,
                            value,
                            continue: () => { i++; emit(); },
                            delete: () => { stores[storeName].delete(primaryKey); },
                        };
                    }
                    if (typeof req.onsuccess === 'function') req.onsuccess({ target: req });
                    tx._settle();
                });
            };
            emit();
            return req;
        }

        return {
            get(key) {
                return issue(() => stores[storeName].get(key));
            },
            put(value) {
                return issue(() => {
                    if (failPutInStore === storeName) {
                        throw new Error('simulated write failure (e.g. quota exceeded)');
                    }
                    stores[storeName].set(keyFor(storeName, value), value);
                    return undefined;
                });
            },
            index(name) {
                return { openCursor: (range) => issueCursor(name, range) };
            },
        };
    }

    function makeTransaction(storeNames) {
        const tx = {
            oncomplete: null,
            onerror: null,
            onabort: null,
            _pending: 0,
            _completed: false,
            _aborted: false,
        };
        tx._settle = () => {
            tx._pending--;
            if (tx._pending === 0 && !tx._aborted && !tx._completed) {
                // Defer to a macrotask, not another microtask: a chained request
                // issued synchronously inside the callback we just ran (e.g. put()
                // called from inside get()'s onsuccess) has already incremented
                // _pending by the time we get here in the simple cases, but
                // recoverStoppedToServer's Promise.all(...).then(...) chain needs
                // several more microtask hops before its value actually lands in
                // withTx's `out` variable. A single queued microtask here can win
                // that race and fire oncomplete() (resolving withTx's promise) with
                // `out` still undefined. setImmediate waits for the whole microtask
                // queue -- including arbitrarily-chained .then()s -- to drain first,
                // same as real IndexedDB's actual auto-commit timing.
                setImmediate(() => {
                    if (tx._pending === 0 && !tx._aborted && !tx._completed) {
                        tx._completed = true;
                        if (typeof tx.oncomplete === 'function') tx.oncomplete();
                    }
                });
            }
        };
        const objectStores = {};
        storeNames.forEach((name) => {
            objectStores[name] = makeObjectStore(name, tx);
        });
        tx.objectStore = (name) => objectStores[name];
        tx.abort = () => {
            tx._aborted = true;
            if (typeof tx.onabort === 'function') tx.onabort();
        };
        return tx;
    }

    return {
        open() {
            const req = { result: undefined, onsuccess: null, onerror: null, onupgradeneeded: null };
            const db = {
                objectStoreNames: { contains: () => true },
                transaction: (storeNames) => makeTransaction(storeNames),
            };
            Promise.resolve().then(() => {
                req.result = db;
                if (typeof req.onsuccess === 'function') req.onsuccess({ target: req });
            });
            return req;
        },
    };
}

function loadModuleWithFakeIndexedDB(fakeIndexedDB) {
    const modPath = path.join(__dirname, 'recording_recovery.js');
    delete require.cache[require.resolve(modPath)];
    global.indexedDB = fakeIndexedDB;
    global.window = undefined; // exercise the Node (non-browser) branch
    const mod = require(modPath);
    delete require.cache[require.resolve(modPath)];
    return mod;
}

async function run() {
    // 1. Happy path: session exists, put succeeds -> true.
    {
        const RecordingRecovery = loadModuleWithFakeIndexedDB(
            makeFakeIndexedDB({ sessionRows: { rec1: { recordingId: 'rec1', expiresAt: 123 } } })
        );
        const blob = { size: 42 };
        const ok = await RecordingRecovery.appendChunk('rec1', 0, blob);
        assert.strictEqual(ok, true, 'expected appendChunk to report true on a real successful write');
    }

    // 2. Missing session row -> must report false, not true.
    {
        const RecordingRecovery = loadModuleWithFakeIndexedDB(makeFakeIndexedDB({ sessionRows: {} }));
        const ok = await RecordingRecovery.appendChunk('does-not-exist', 0, { size: 1 });
        assert.strictEqual(ok, false, 'expected appendChunk to report false when the session row is missing');
    }

    // 3. The chunk write itself fails (e.g. storage quota) -> must report
    //    false, not throw uncaught and not report true.
    {
        const RecordingRecovery = loadModuleWithFakeIndexedDB(
            makeFakeIndexedDB({
                sessionRows: { rec1: { recordingId: 'rec1', expiresAt: 123 } },
                failPutInStore: 'chunks',
            })
        );
        const ok = await RecordingRecovery.appendChunk('rec1', 0, { size: 1 });
        assert.strictEqual(ok, false, 'expected appendChunk to report false when the underlying chunk write fails');
    }

    // 4. No recordingId / no blob -> false without touching IndexedDB at all.
    {
        const RecordingRecovery = loadModuleWithFakeIndexedDB(makeFakeIndexedDB({}));
        assert.strictEqual(await RecordingRecovery.appendChunk(null, 0, { size: 1 }), false);
        assert.strictEqual(await RecordingRecovery.appendChunk('rec1', 0, null), false);
    }

    // 5. P2-6 zero-byte guard: a 'stopped' session with zero chunks (e.g. the
    //    tab crashed before the first timeslice ever fired) assembles into a
    //    valid but empty File. recoverStoppedToServer() must not upload it or
    //    count it as recovered -- that would tell a doctor audio was saved
    //    when nothing actually was.
    {
        const RecordingRecovery = loadModuleWithFakeIndexedDB(
            makeFakeIndexedDB({
                sessionRows: {
                    rec1: {
                        recordingId: 'rec1',
                        userKey: 'u1',
                        encounterId: 'e1',
                        status: 'stopped',
                        expiresAt: Date.now() + 100000,
                        mimeType: 'audio/webm',
                        fileName: 'rec1.webm',
                    },
                },
                chunkRows: {},
            })
        );
        let uploadCalls = 0;
        const res = await RecordingRecovery.recoverStoppedToServer({
            userKey: 'u1',
            encounterId: 'e1',
            uploadRecording: async () => { uploadCalls++; return { ok: true, mode: 'server' }; },
        });
        assert.strictEqual(uploadCalls, 0, 'expected a zero-byte recording to never be uploaded');
        assert.strictEqual(res.recovered, 0, 'expected a zero-byte recording to not count as recovered');
    }

    // 6. Sanity check on the same path: a session WITH real chunk data must
    //    still be recovered normally (the zero-byte guard must not swallow
    //    genuine recordings).
    {
        const RecordingRecovery = loadModuleWithFakeIndexedDB(
            makeFakeIndexedDB({
                sessionRows: {
                    rec2: {
                        recordingId: 'rec2',
                        userKey: 'u1',
                        encounterId: 'e1',
                        status: 'stopped',
                        expiresAt: Date.now() + 100000,
                        mimeType: 'audio/webm',
                        fileName: 'rec2.webm',
                    },
                },
                chunkRows: {
                    'rec2:0': { recordingId: 'rec2', seq: 0, blob: new Blob(['real audio bytes']) },
                },
            })
        );
        let uploadedSize = null;
        const res = await RecordingRecovery.recoverStoppedToServer({
            userKey: 'u1',
            encounterId: 'e1',
            uploadRecording: async (data) => { uploadedSize = data.file.size; return { ok: true, mode: 'server' }; },
        });
        assert.ok(uploadedSize > 0, 'expected the non-empty recording to be uploaded');
        assert.strictEqual(res.recovered, 1, 'expected the non-empty recording to count as recovered');
    }

    console.log('recording_recovery.test.cjs: all assertions passed');
}

run().catch((e) => {
    console.error(e);
    process.exit(1);
});
