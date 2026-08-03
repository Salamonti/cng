/**
 * When the cursor is over a nested scroll box that has reached its scroll extent,
 * browsers often stop scrolling (no chain to the document). Forward wheel deltas
 * to the main page in that case so users can continue down/up the workspace.
 */
(function () {
  function skipChain(el) {
    if (!el || !el.closest) return true;
    return !!(
      el.closest('#settingsDrawer') ||
      el.closest('#serviceErrorBanner') ||
      el.closest('#promptModal') ||
      el.closest('#templateModal') ||
      el.closest('#cameraModal') ||
      el.closest('#orderRequestsModal') ||
      el.closest('#feedbackSuggestionModal') ||
      el.closest('#qaSidePanel') ||
      el.closest('.tools-sheet') ||
      el.closest('iframe')
    );
  }

  function findVerticalScrollHost(start) {
    for (let el = start; el && el !== document.documentElement; el = el.parentElement) {
      if (el === document.body) continue;
      if (skipChain(el)) return null;
      const st = window.getComputedStyle(el);
      const oy = st.overflowY;
      if (oy !== 'auto' && oy !== 'scroll' && oy !== 'overlay') continue;
      if (el.scrollHeight > el.clientHeight + 1) return el;
    }
    return null;
  }

  document.addEventListener(
    'wheel',
    function (e) {
      if (e.ctrlKey || e.metaKey) return;
      const dy = e.deltaY;
      if (!dy) return;

      const host = findVerticalScrollHost(e.target);
      if (!host) return;

      const top = host.scrollTop <= 0;
      const bottom = host.scrollTop + host.clientHeight >= host.scrollHeight - 2;

      if ((dy < 0 && top) || (dy > 0 && bottom)) {
        e.preventDefault();
        window.scrollBy({ top: dy, left: e.deltaX || 0, behavior: 'auto' });
      }
    },
    { capture: true, passive: false }
  );
})();
