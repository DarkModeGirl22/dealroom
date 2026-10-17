// The bell: DealRoom's notes from all of this person's deals, with a red count of new ones,
// and a short banner when something new happens. The chat itself only holds the two people's messages.
(function () {
  const bell = document.getElementById('bell');
  if (!bell) return;
  const panel = document.getElementById('bell-panel'), list = document.getElementById('bell-list');
  const count = document.getElementById('bell-count'), toast = document.getElementById('toast');
  const as = new URLSearchParams(location.search).get('as') || document.body.dataset.as || 'maya';
  let newest = 0, first = true, toastTimer = null;
  const esc = s => (s || '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const when = ts => {
    try {
      const d = new Date(ts), t = d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
      return d.toDateString() === new Date().toDateString() ? t : d.toLocaleDateString([], {month: 'short', day: 'numeric'}) + ', ' + t;
    } catch (e) { return ''; }
  };

  function show(notes, unread) {
    count.hidden = !unread;
    count.textContent = unread > 9 ? '9+' : unread;
    bell.setAttribute('aria-label', unread ? `Notifications, ${unread} new` : 'Notifications');
    list.innerHTML = notes.length ? notes.map(n => `
      <li class="${n.level || 'info'} ${n.unread ? 'new' : ''}">
        <a href="/r/${n.room_id}?as=${as}">
          <span class="meta">${esc(n.deal)} · ${esc(n.with)} · ${when(n.ts)}${n.private ? ' · only you' : ''}</span>
          <span class="text">${esc(n.text)}</span>
        </a></li>`).join('') : '<li class="empty">Nothing yet. Payments, deliveries and warnings show up here.</li>';
    const top = notes.length ? notes[0].id : 0;
    if (!first && top > newest) {   // something new happened while you were here: say it once, briefly
      const n = notes[0];
      toast.className = `toast ${n.level || 'info'}`;
      toast.innerHTML = `<strong>${esc(n.deal)}</strong> ${esc(n.text)}`;
      toast.hidden = false;
      clearTimeout(toastTimer);
      toastTimer = setTimeout(() => { toast.hidden = true; }, 6000);
    }
    newest = Math.max(newest, top);
    first = false;
  }
  window.dealroomNotes = show;   // the room page passes along what it already fetched

  async function poll() {
    if (window.dealroomPoll) return;   // on a room page, room.js fetches the notes with the chat
    try {
      const r = await fetch(`/notifications?as=${as}`);
      const d = await r.json();
      show(d.notes, d.unread);
    } catch (e) { /* next poll retries */ }
  }
  bell.addEventListener('click', async () => {
    const open = panel.hidden;
    panel.hidden = !open;
    bell.setAttribute('aria-expanded', String(open));
    if (open && newest) {
      await fetch('/notifications/seen', {method: 'POST', body: new URLSearchParams({as, upto: newest})});
      count.hidden = true;
    }
  });
  document.addEventListener('click', e => {
    if (!panel.hidden && !e.target.closest('.bell-wrap')) { panel.hidden = true; bell.setAttribute('aria-expanded', 'false'); }
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') panel.hidden = true; });
  toast.addEventListener('click', () => { toast.hidden = true; });
  setTimeout(poll, 50);
  setInterval(poll, 3000);
})();
