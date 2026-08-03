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
        document.querySelectorAll('#mobileBottomNav .nav-item:not(.danger)').forEach(n => n.classList.remove('active'));
        el.classList.add('active');
      }
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
        } catch {}
      }
