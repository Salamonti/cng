// Fills #appVersionLabel from GET /api/version (commit + build timestamp)
(function loadRuntimeVersion() {
  const label = document.getElementById('appVersionLabel');
  if (!label) return;
  // Skip if already populated (avoids layout shift from text change)
  if (label.textContent && label.textContent.length > 20) return;
  fetch(`/api/version?ts=${Date.now()}`, { cache: 'no-store' })
    .then((resp) => {
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      return resp.json();
    })
    .then((data) => {
      const raw = String(data?.commit_hash || '').trim();
      const shortCommit =
        raw && raw.toLowerCase() !== 'unknown' ? raw.slice(0, 7) : '';
      const buildTs = String(data?.build_timestamp_utc || '').trim();
      const appVer = String(data?.app_version || '').trim();
      const parts = ['DreamCision AI\u00B7SCRIBE'];
      if (appVer) parts.push('v' + appVer);
      if (shortCommit) parts.push(shortCommit);
      if (buildTs) parts.push(buildTs);
      label.textContent = parts.join(' · ');
    })
    .catch(() => {});
})();
