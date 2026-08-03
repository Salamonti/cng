import assert from 'node:assert/strict';
import test from 'node:test';

import { toApiNoteType } from '../web/js/note_type_mapping.mjs';

test('toApiNoteType passes through custom note type ids', () => {
  assert.equal(toApiNoteType('my_custom_note_type'), 'my_custom_note_type');
});

test('toApiNoteType maps known legacy aliases', () => {
  assert.equal(toApiNoteType('progress_note'), 'progress');
  assert.equal(toApiNoteType('progress note'), 'progress');
  assert.equal(toApiNoteType('consultation'), 'consult');
});

test('toApiNoteType defaults to consult for empty input', () => {
  assert.equal(toApiNoteType(''), 'consult');
  assert.equal(toApiNoteType(null), 'consult');
  assert.equal(toApiNoteType(undefined), 'consult');
});

