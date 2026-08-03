const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function loadPipeline() {
  const sandbox = {
    console,
    Date,
    Promise,
    setTimeout,
    clearTimeout,
  };
  sandbox.globalThis = sandbox;
  const source = fs.readFileSync(path.join(__dirname, 'asr_chunk_pipeline.js'), 'utf8');
  vm.runInNewContext(source, sandbox, { filename: 'asr_chunk_pipeline.js' });
  return sandbox.AsrChunkPipeline;
}

test('drain recovery publishes the merged transcript after a chunk worker failure', async () => {
  const AsrChunkPipeline = loadPipeline();
  const statuses = [];
  const transcripts = [];
  let fetchMergedCalls = 0;
  const mergedText = 'Recovered final transcript.';

  const pipeline = new AsrChunkPipeline({
    transcribeFn: async () => {
      throw new Error('temporary chunk transcription failure');
    },
    fetchMergedFn: async () => {
      fetchMergedCalls += 1;
      return '';
    },
    drainFn: async (encounterId, sessionId) => {
      assert.equal(encounterId, 'encounter-1');
      assert.equal(sessionId, 'recording-1');
      return { merged_transcript_text: mergedText };
    },
    onStatus: (status) => statuses.push(status),
    onTranscript: (text, meta) => transcripts.push({ text, meta }),
  });

  pipeline.start({ encounterId: 'encounter-1', sessionId: 'recording-1' });
  pipeline._enqueueTranscribe('segment-1', 0);
  await pipeline.waitUntilIdle();

  assert.deepEqual(statuses, ['transcribing', 'error']);
  assert.deepEqual(transcripts, []);

  await pipeline.ensureSessionComplete();

  const delivered = transcripts.map(({ text, meta }) => ({ text, final: meta.final }));
  assert.deepEqual(delivered, [{ text: mergedText, final: true }]);
  assert.equal(await pipeline.fetchMerged(), mergedText);
  assert.equal(fetchMergedCalls, 0);
});
