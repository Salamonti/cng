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

function makeFakeIndexedDB({ sessionRows = {}, failPutInStore = null } = {}) {
    const stores = {
        sessions: new Map(Object.entries(sessionRows)),
        chunks: new Map(),
    };

    function keyFor(storeName, value) {
        return storeName === 'sessions' ? value : `${value.recordingId}:${value.seq}`;
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
                // Defer once more: a chained request issued synchronously inside the
                // callback we just ran (e.g. put() called from inside get()'s
                // onsuccess) has already incremented _pending by the time we get
                // here, so this only fires once nothing further was queued.
                Promise.resolve().then(() => {
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

    console.log('recording_recovery.test.cjs: all assertions passed');
}

run().catch((e) => {
    console.error(e);
    process.exit(1);
});
