'use strict';

const assert = require('assert');
const path = require('path');

// Load browser-oriented script in Node (sets module.exports from dual-build block).
const durPath = path.join(__dirname, 'recording_durability.js');
const { computeRecordingDurable } = require(durPath);

assert.strictEqual(computeRecordingDurable(null), false);
assert.strictEqual(computeRecordingDurable({}), false);

assert.strictEqual(computeRecordingDurable({ persistLayerOk: true }), true);
assert.strictEqual(computeRecordingDurable({ persistLayerOk: false }), false);

assert.strictEqual(computeRecordingDurable({ transcribeOk: true }), true);
assert.strictEqual(computeRecordingDurable({ transcribeOk: false }), false);

assert.strictEqual(
    computeRecordingDurable({
        persistLayerOk: false,
        transcribeOk: false,
    }),
    false
);

assert.strictEqual(
    computeRecordingDurable({
        persistLayerOk: false,
        transcribeOk: true,
    }),
    true
);

console.log('recording_durability.test.cjs: all assertions passed');
