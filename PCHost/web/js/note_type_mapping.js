/**
 * Frontend → API note type mapping.
 *
 * Built-ins are sent as-is; legacy aliases can be normalized here.
 * Critically: user-defined note type ids must pass through unchanged.
 *
 * This file is loaded as a plain browser script (not an ES module).
 */
(function () {
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

  function toApiNoteType(noteType) {
    const raw = String(noteType ?? '').trim();
    if (!raw) return 'consult';
    // Pass through unknown ids verbatim (custom note types).
    return routeTypeMap[raw] || raw;
  }

  try {
    window.toApiNoteType = toApiNoteType;
  } catch (_) {
    // ignore (non-browser)
  }
})();

