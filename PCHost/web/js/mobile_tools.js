      // ===== Mobile v5 helpers: bottom nav + tools sheet =====
      function openToolsSheet() {
        const back = document.getElementById('toolsSheetBackdrop');
        const sheet = document.getElementById('toolsSheet');
        if (back) back.classList.add('open');
        if (sheet) sheet.classList.add('open');
      }
      function closeToolsSheet() {
        const back = document.getElementById('toolsSheetBackdrop');
        const sheet = document.getElementById('toolsSheet');
        if (back) back.classList.remove('open');
        if (sheet) sheet.classList.remove('open');
      }
      function mobileNavSetActive(el) {
        if (!el || el.classList.contains('danger')) return;
        document.querySelectorAll('#mobileBottomNav .nav-item:not(.danger)').forEach(n => {
          n.classList.remove('active');
          n.removeAttribute('aria-current');
        });
        el.classList.add('active');
        el.setAttribute('aria-current', 'true');
      }

      // Bottom nav items, Tools sheet items, and patient-material category
      // cards are divs with role="button" (their existing layout/markup
      // isn't valid inside a native <button>), so unlike a real button they
      // get no built-in Enter/Space activation. Delegate it here instead of
      // wiring a keydown handler onto every element individually.
      document.addEventListener('keydown', (e) => {
        if (e.key !== 'Enter' && e.key !== ' ' && e.key !== 'Spacebar') return;
        const target = e.target.closest('[role="button"][tabindex]');
        if (!target) return;
        e.preventDefault();
        target.click();
      });
      function mobileNavGo(which, el) {
        try {
          if (el) mobileNavSetActive(el);
          closeToolsSheet();
          if (which === 'chart') {
            const t = document.getElementById('chartCard') || document.getElementById('authCard');
            if (t) t.scrollIntoView({ behavior: 'smooth', block: 'start' });
            return;
          }
          if (which === 'note') {
            const t = document.getElementById('noteCard');
            if (t) t.scrollIntoView({ behavior: 'smooth', block: 'start' });
            return;
          }
        } catch {
          // (a) Best-effort mobile nav: a scrollIntoView-with-options throw (old
          // browser) must not break navigation activation itself.
        }
      }
