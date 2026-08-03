function openQAPanel(){
  const p=document.getElementById('qaSidePanel');
  const b=document.getElementById('qaSideBackdrop');
  const nav=document.getElementById('mobileBottomNav');
  if(!p||!b){ window.location.href='qa.html'; return; }
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
