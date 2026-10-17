(function () {
  const root = document.querySelector('.layout');
  if (!root) return;
  const roomId = root.dataset.room, as = root.dataset.as, me = Number(root.dataset.me);
  const feed = document.getElementById('feed'), card = document.getElementById('card');
  const form = document.getElementById('say'), input = document.getElementById('text');
  let last = 0, busy = false, lastDeal = null;

  const esc = s => (s || '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const time = ts => {
    try {
      const d = new Date(ts), t = d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
      return d.toDateString() === new Date().toDateString() ? t : d.toLocaleDateString([], {month: 'short', day: 'numeric'}) + ', ' + t;
    } catch (e) { return ''; }
  };

  function render(m) {
    if (m.deal !== lastDeal) {   // one conversation, several deals: say where each one starts
      if (lastDeal !== null || feed.dataset.multi === '1') {
        const dv = document.createElement('div');
        dv.className = 'divider';
        dv.textContent = m.deal;
        feed.appendChild(dv);
      }
      lastDeal = m.deal;
    }
    const el = document.createElement('div');
    if (m.kind === 'chat') {
      el.className = 'msg ' + (m.sender_id === me ? 'mine' : 'theirs');
      el.innerHTML = `<div class="from">${esc(m.sender)} <span>${time(m.ts)}</span></div><div class="body">${esc(m.text)}</div>`;
    } else {
      el.className = `note ${m.kind === 'private' ? 'priv' : ''} ${m.level || 'info'}`;
      const tag = m.kind === 'private' ? 'Only you can see this' : 'DealRoom';
      el.innerHTML = `<div class="tag">${tag} · ${time(m.ts)}</div><div class="body">${esc(m.text)}</div>`;
    }
    feed.appendChild(el);
  }

  async function poll() {
    if (busy) return;
    busy = true;
    try {
      const r = await fetch(`/r/${roomId}/feed?as=${as}&after=${last}`);
      const d = await r.json();
      const atBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 60;
      feed.dataset.multi = d.multi ? '1' : '0';
      d.messages.forEach(m => { render(m); last = Math.max(last, m.id); });
      if (d.messages.length && (atBottom || last === d.messages.at(-1).id)) feed.scrollTop = feed.scrollHeight;
      if (card.dataset.html !== d.card && !card.dataset.hold) { card.dataset.html = d.card; card.innerHTML = d.card; buttons(); }
    } catch (e) { /* offline for a moment: next poll retries */ }
    busy = false;
  }

  const priv = document.getElementById('private');
  async function send(text, checked) {
    input.value = '';
    priv.hidden = true;
    await fetch(`/r/${roomId}/say`, {method: 'POST', body: new URLSearchParams({text, as, checked: checked ? '1' : '0'})});
    poll();
  }
  form.addEventListener('submit', async e => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    // the private honesty check: only this person sees it, and it never blocks the message
    const r = await fetch(`/r/${roomId}/precheck`, {method: 'POST', body: new URLSearchParams({text, as})});
    const d = await r.json().catch(() => ({}));
    if (!d.warning) return send(text);
    priv.innerHTML = `<div class="tag">Only you can see this</div><div>${esc(d.warning)}</div>
      <div class="row"><button type="button" class="btn secondary" id="p-edit">Edit my message</button>
      <button type="button" class="btn" id="p-send">Send anyway</button></div>`;
    priv.hidden = false;
    input.focus();
    document.getElementById('p-edit').onclick = () => { priv.hidden = true; input.focus(); };
    document.getElementById('p-send').onclick = () => send(text, true);
  });

  // actions inside the deal card (accept, pay, upload...) post and refresh
  card.addEventListener('submit', async e => {
    const f = e.target;
    if (!f.matches('form[data-ajax]')) return;
    e.preventDefault();
    const fd = new FormData(f);
    fd.set('as', as);
    f.querySelectorAll('button').forEach(b => b.disabled = true);
    try {
      const r = await fetch(f.action, {method: 'POST', body: fd});
      const d = await r.json().catch(() => ({}));
      if (d.go) { location.href = d.go; return; }
      if (d.warning) {   // the private honesty check, before a dispute is opened
        priv.innerHTML = `<div class="tag">Only you can see this</div><div>${esc(d.warning)}</div>`;
        priv.hidden = false;
        if (f.elements.confirm) f.elements.confirm.value = '1';
        f.querySelectorAll('button').forEach(b => { b.disabled = false; b.textContent = 'Open the dispute anyway'; });
        card.dataset.hold = '1';
        return;
      }
      card.dataset.hold = '';
    } finally { poll(); }
  });

  // choosing a file delivers it right away (one action, one control)
  card.addEventListener('change', e => {
    if (e.target.matches('input[type=file]') && e.target.files.length) e.target.form.requestSubmit();
  });

  // PayPal button: the order is created by the server from the accepted terms, so the amount is locked
  function buttons() {
    const box = document.getElementById('pp-buttons');
    if (!box || !window.paypal || box.dataset.done) return;
    box.dataset.done = '1';
    const part = box.dataset.part;
    window.paypal.Buttons({
      style: {layout: 'vertical', height: 40, label: 'pay'},
      createOrder: async () => {
        const r = await fetch(`/r/${roomId}/pay/${part}/order?as=${as}`, {method: 'POST'});
        const d = await r.json();
        if (!d.id) throw new Error(d.error || 'order failed');
        return d.id;
      },
      onApprove: async data => {
        await fetch(`/r/${roomId}/pay/capture?as=${as}`, {method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({order_id: data.orderID})});
        poll();
      }
    }).render(box);
  }

  poll();
  setInterval(poll, 1500);
  window.dealroomPoll = poll;
})();
