/**
 * Mirrors guard order in generate_ui_flow.js triggerGenerateNoteAndFocus (no DOM).
 * Run: npm test  (from PCHost/)
 */
import assert from 'node:assert/strict';
import test from 'node:test';

function isPipelineBusyWithoutCapturePhase(phase) {
  return phase === 'stopping' || phase === 'transcribing' || phase === 'generating_note';
}

/** Returns block reason or null if Generate may proceed to branch logic. */
function simulateGuards({ capturing, pending, phase }) {
  if (pending && !capturing) return 'duplicate-chain';
  if (isPipelineBusyWithoutCapturePhase(phase) && !capturing && !pending) return 'pipeline-wait';
  return null;
}

test('blocks duplicate Generate while chained transcribe (pending, mic off)', () => {
  assert.equal(
    simulateGuards({ capturing: false, pending: true, phase: 'transcribing' }),
    'duplicate-chain',
  );
});

test('blocks fresh Generate during transcribe without pending (manual stop path)', () => {
  assert.equal(
    simulateGuards({ capturing: false, pending: false, phase: 'transcribing' }),
    'pipeline-wait',
  );
});

test('allows Generate while recording (pending false, mic on)', () => {
  assert.equal(simulateGuards({ capturing: true, pending: false, phase: 'recording' }), null);
});

test('allows idle Generate', () => {
  assert.equal(simulateGuards({ capturing: false, pending: false, phase: 'idle' }), null);
});
