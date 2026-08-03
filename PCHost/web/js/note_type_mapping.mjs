/**
 * ESM version of the note-type mapping helper for Node tests.
 * Keep logic in sync with `note_type_mapping.js`.
 */

const routeTypeMap = {
  progress: 'progress',
  followup: 'followup',
  consult: 'consult',
  procedure: 'procedure',
  referral: 'referral',
  admission: 'admission',
  discharge: 'discharge',
  transfer: 'transfer',
  summarize: 'summarize',
  custom: 'custom',
  // legacy / backwards-compat convenience
  progress_note: 'progress',
  'progress note': 'progress',
  consultation: 'consult',
  multi_issue_soap: 'multi_issue_soap',
  'multi-issue soap': 'multi_issue_soap',
  pre_encounter_prep: 'pre_encounter_prep',
  'pre-encounter preparation': 'pre_encounter_prep',
  'pre encounter prep': 'pre_encounter_prep',
};

export function toApiNoteType(noteType) {
  const raw = String(noteType ?? '').trim();
  if (!raw) return 'consult';
  return routeTypeMap[raw] || raw;
}

