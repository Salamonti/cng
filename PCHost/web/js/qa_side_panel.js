function openQAPanel(){
  const p=document.getElementById('qaSidePanel');
  const b=document.getElementById('qaSideBackdrop');
  const nav=document.getElementById('mobileBottomNav');
  if(!p||!b){ window.location.href='qa.html'; return; }
  // Re-request the iframe each time the panel opens. The frame is embedded in
  // the initial DOM, so it first loads pre-login and gets a 401 from PCHost's
  // auth gate; by the time the user logs in and opens Q&A, that gated response
  // is stale. Reloading with the now-present dc_session cookie lets PCHost
  // serve the real qa.html into the panel.
  const frame=document.getElementById('qaSideFrame');
  if(frame){
    const cur=frame.getAttribute('src')||'qa.html';
    // Strip any previous cache-bust to keep the URL stable-ish but force refetch.
    const base=cur.split('?')[0];
    frame.src=base+'?v='+Date.now();
  }
  p.classList.add('open'); b.classList.add('open'); p.setAttribute('aria-hidden','false');
  if(nav) nav.style.display='none';
}
function printQAPanel(){
  const frame=document.getElementById('qaSideFrame');
  try {
    if(frame&&frame.contentWindow&&typeof frame.contentWindow.printLastQaAnswer==='function'){
      frame.contentWindow.printLastQaAnswer();
      return;
    }
  } catch(e) { console.warn('[QA panel] iframe print failed', e); }
}
function closeQAPanel(){
  const p=document.getElementById('qaSidePanel');
  const b=document.getElementById('qaSideBackdrop');
  const nav=document.getElementById('mobileBottomNav');
  if(!p||!b) return;
  p.classList.remove('open'); b.classList.remove('open'); p.setAttribute('aria-hidden','true');
  if(nav) nav.style.display='';
}
