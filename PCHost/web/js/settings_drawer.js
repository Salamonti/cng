// Settings drawer: profile fields, auth card placement, mobile top bar (main workspace only)
(function () {
  'use strict';

  async function loadProfileIntoDrawer() {
    const auth = window.AuthWorkspace;
    const st = document.getElementById('profileSaveStatus');
    if (st) st.textContent = '';
    if (!auth || typeof auth.request !== 'function') return;
    try {
      const apiBase =
        typeof getApiBase === 'function'
          ? getApiBase(window.app?.settings?.serverUrl || '/api')
          : '/api';
      const resp = await auth.request(apiBase.replace(/\/?$/, '') + '/profile', { method: 'GET' });
      if (!resp.ok) return;
      const p = await resp.json();
      const dn = document.getElementById('profileDisplayName');
      const sp = document.getElementById('profileSpecialty');
      const loc = document.getElementById('profileLocation');
      if (dn) dn.value = p.display_name || '';
      if (sp) sp.value = p.default_specialty || '';
      if (loc) loc.value = p.default_location || '';
      const bar = document.getElementById('userSpeciality');
      if (bar && !(bar.value || '').trim() && (p.default_specialty || '').trim()) {
        bar.value = (p.default_specialty || '').trim();
      }
    } catch (e) {
      console.warn('[Profile] drawer load', e);
    }
  }

  async function saveProfileFieldsFromDrawer() {
    const auth = window.AuthWorkspace;
    const st = document.getElementById('profileSaveStatus');
    if (!auth || typeof auth.request !== 'function') {
      if (st) st.textContent = 'Sign in to save.';
      return;
    }
    const dn = document.getElementById('profileDisplayName')?.value ?? '';
    const sp = document.getElementById('profileSpecialty')?.value ?? '';
    const loc = document.getElementById('profileLocation')?.value ?? '';
    try {
      const apiBase =
        typeof getApiBase === 'function'
          ? getApiBase(window.app?.settings?.serverUrl || '/api')
          : '/api';
      const base = apiBase.replace(/\/?$/, '');
      const resp = await auth.request(base + '/profile', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ display_name: dn, default_specialty: sp, default_location: loc }),
      });
      if (st) st.textContent = resp.ok ? 'Saved.' : 'Error ' + resp.status;
      if (resp.ok) {
        const bar = document.getElementById('userSpeciality');
        if (bar) bar.value = (sp || '').trim();
      }
    } catch (e) {
      if (st) st.textContent = 'Save failed.';
    }
  }

  function openSettingsDrawer() {
    const d = document.getElementById('settingsDrawer');
    const b = document.getElementById('settingsBackdrop');
    if (!d || !b) return;
    d.classList.add('open');
    b.classList.add('open');
    d.setAttribute('aria-hidden', 'false');
    loadProfileIntoDrawer();
  }

  function closeSettingsDrawer() {
    const d = document.getElementById('settingsDrawer');
    const b = document.getElementById('settingsBackdrop');
    if (!d || !b) return;
    d.classList.remove('open');
    b.classList.remove('open');
    d.setAttribute('aria-hidden', 'true');
  }

  function _hasToken() {
    try {
      return !!(
        sessionStorage.getItem('auth_access_token') ||
        localStorage.getItem('access_token') ||
        (window.app && window.app.settings && window.app.settings.apiKey)
      );
    } catch (e) {
      return false;
    }
  }

  function placeAuthCard(signedIn) {
    try {
      const card = document.getElementById('authCard');
      const mount = document.getElementById('settingsAuthMount');
      const slot = document.getElementById('authCardSlot');
      if (!card || !mount || !slot) return;
      if (signedIn) {
        mount.appendChild(card);
        card.dataset.location = 'drawer';
      } else {
        slot.parentNode.insertBefore(card, slot.nextSibling);
        card.dataset.location = 'main';
      }
      card.style.display = '';
    } catch (e) {}
  }

  function isMobile() {
    try {
      return window.matchMedia && window.matchMedia('(max-width: 850px)').matches;
    } catch (e) {
      return false;
    }
  }

  function applyMobileBarPolicy(signedIn) {
    if (!signedIn) {
      document.body.classList.remove('mobile-bar-collapsed');
      return;
    }
    if (isMobile()) document.body.classList.add('mobile-bar-collapsed');
  }

  function toggleMobileTopBar() {
    if (!isMobile()) return;
    document.body.classList.toggle('mobile-bar-collapsed');
  }

  window.openSettingsDrawer = openSettingsDrawer;
  window.closeSettingsDrawer = closeSettingsDrawer;
  window.saveProfileFieldsFromDrawer = saveProfileFieldsFromDrawer;
  window.toggleMobileTopBar = toggleMobileTopBar;

  document.addEventListener('DOMContentLoaded', () => {
    const initialSignedIn = _hasToken();
    placeAuthCard(initialSignedIn);
    applyMobileBarPolicy(initialSignedIn);

    window.addEventListener('workspace-auth-changed', (ev) => {
      const signedIn = !!(ev && ev.detail && ev.detail.signedIn);
      placeAuthCard(signedIn);
      applyMobileBarPolicy(signedIn);
    });

    setTimeout(() => {
      const signedInNow = _hasToken();
      placeAuthCard(signedInNow);
      applyMobileBarPolicy(signedInNow);
    }, 600);
  });
})();
