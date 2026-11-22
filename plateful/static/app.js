/* Plateful front-end - vanilla JS, no external requests (works fully offline). */
const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const SLOTS = ['breakfast', 'lunch', 'dinner', 'snack'];
const AISLES = ['Produce', 'Meat & Seafood', 'Dairy & Eggs', 'Bakery', 'Pantry', 'Canned & Dry', 'Refrigerated', 'Snacks', 'Frozen', 'Other'];
const FLAGS = ['meat', 'fish', 'shellfish', 'dairy', 'egg', 'gluten', 'nut', 'soy', 'animal'];
const S = { tab: 'plan', ws: null, plan: null, recipes: [], pb: [], people: [], boot: null, wiz: null, rfilter: { q: '', slot: '' }, pq: '' };

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const $ = (s, el = document) => el.querySelector(s);
const cap = s => s.charAt(0).toUpperCase() + s.slice(1);
const money = n => (S.boot ? S.boot.settings.currency : '$') + Number(n || 0).toFixed(2);
const num = n => String(Math.round(n * 100) / 100);

S.user = null;
try { document.documentElement.dataset.theme = localStorage.getItem('plateful-theme') || 'dark'; } catch (e) { document.documentElement.dataset.theme = 'dark'; }
const ICONS = {
  lock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 018 0v3"/>',
  unlock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 017.5-2"/>',
  swap: '<path d="M4 8h14l-3.5-3.5M20 16H6l3.5 3.5"/>',
  up: '<path d="M7 11v9H4v-9zM7 11l4-8c2 0 3 1 3 3v3h5.5a1.5 1.5 0 011.5 1.7l-1 7A2 2 0 0116.5 20H7"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  plan: '<rect x="3.5" y="5" width="17" height="15" rx="2.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
  cart: '<circle cx="9" cy="20" r="1.4"/><circle cx="18" cy="20" r="1.4"/><path d="M3 4h2.5l2.2 11h10.3l2-8H6.3"/>',
  book: '<path d="M5 4h11a3 3 0 013 3v13H8a3 3 0 01-3-3z"/><path d="M5 17a3 3 0 013-3h11"/>',
  users: '<circle cx="9" cy="8" r="3.2"/><path d="M3 20c0-3.4 2.7-6 6-6s6 2.600 6 6M16 5.200a3 3 0 010 5.600M18 14.300c1.900.8 3 2.700 3 5.700"/>',
  tag: '<path d="M3 12V4h8l10 10-8 8z"/><circle cx="7.500" cy="8.500" r="1.200"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M12 2.500v3M12 18.500v3M2.500 12h3M18.500 12h3M5.300 5.300l2.100 2.100M16.600 16.600l2.100 2.100M5.300 18.700l2.100-2.100M16.600 7.400l2.100-2.100"/>',
  ext: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 01-1 1H5a1 1 0 01-1-1V7a1 1 0 011-1h5"/>'
};
const BRAND_SVG = '<svg class="logo" viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="9" style="fill:var(--accent)"/><circle cx="16" cy="17" r="8" fill="none" stroke="var(--accent-ink)" stroke-width="2.2"/><path d="M16 17c0-5.500 3.200-8.600 8.400-8.600-.2 5.600-3.200 8.600-8.400 8.600z" style="fill:var(--accent-ink)"/></svg>';
const ic = (n, cls = '') => `<svg class="ic ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICONS[n]}</svg>`;

async function api(method, url, body) {
  const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json', 'X-Plateful': '1' }, body: body !== undefined ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (r.status === 401 && !url.startsWith('/api/auth/')) { S.user = null; showAuth('login'); throw new Error('Please sign in'); }
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(toast.h); toast.h = setTimeout(() => t.classList.remove('show'), 2600);
}
async function guard(fn) { try { return await fn(); } catch (e) { toast(e.message); } }

const addDays = (iso, n) => { const d = new Date(iso + 'T00:00:00'); d.setDate(d.getDate() + n); return fmtISO(d); };
const fmtISO = d => d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
const longDate = iso => new Date(iso + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' });

/* ---------------------------------------------------------------- modal */
function openModal(html, wide) {
  $('#modal-root').innerHTML = `<div class="overlay" data-a="overlay"><div class="modal ${wide ? 'wide' : ''}">${html}</div></div>`;
}
function closeModal() { $('#modal-root').innerHTML = ''; }

/* ---------------------------------------------------------------- boot */
async function loadBase() {
  [S.boot, S.recipes, S.pb] = await Promise.all([api('GET', '/api/bootstrap'), api('GET', '/api/recipes'), api('GET', '/api/pricebook')]);
  S.people = S.boot.people;
  if (!S.ws) S.ws = S.boot.this_week;
}
async function init() {
  await guard(async () => {
    const st = await api('GET', '/api/auth/status');
    S.user = st.user; S.signupOpen = st.signup_open;
    if (!st.has_users) return showAuth('setup');
    if (!st.user) {
      const inv = new URLSearchParams(location.search).get('invite');
      return inv ? showAuth('register', inv) : showAuth('login');
    }
    await startApp();
  });
}
async function startApp() {
  await loadBase();
  renderWho();
  if (!S.boot.settings.setup_done) return startWizard();
  await show('plan');
}
function renderWho() {
  $('#whoami').innerHTML = S.user ? `<span><b>${esc(S.user.username)}</b></span><button class="ghost" data-a="logout">Sign out</button>` : '';
}
function showAuth(mode, code) {
  $('#nav').innerHTML = ''; $('#whoami').innerHTML = ''; closeModal();
  const setup = mode === 'setup', reg = mode === 'register', fam = mode === 'signup', newpw = setup || reg || fam;
  const intro = setup ? 'Welcome! Create the admin account for this server.'
    : reg ? 'Create your account with the invite code you were given. You’ll join their family.'
    : fam ? 'Start your own family. Your plans, recipes and prices stay private to your family.' : 'Sign in to continue.';
  const links = setup ? '' : `<p class="small muted" style="text-align:center;margin:14px 0 0">${
    mode === 'login' ? `Have an invite code? <a href="#" data-a="authmode" data-mode="register">Join a family</a>${S.signupOpen ? ' · New here? <a href="#" data-a="authmode" data-mode="signup">Create your own family</a>' : ''}`
      : `Already have an account? <a href="#" data-a="authmode" data-mode="login">Sign in</a>`}</p>`;
  $('#main').innerHTML = `<div class="auth card"><div class="brand">${BRAND_SVG}<span>Plateful</span></div>
    <p class="muted" style="text-align:center;margin-top:0">${intro}</p>
    <div class="formgrid">
    ${reg ? `<label class="full">Invite code<input id="au-code" value="${esc(code || '')}" autocapitalize="none" spellcheck="false" autocomplete="off"></label>` : ''}
    <label class="full">${reg ? 'Choose a username' : 'Username'}<input id="au-user" autocomplete="username" autocapitalize="none" spellcheck="false"></label>
    <label class="full">Password${newpw ? ' (8+ characters)' : ''}<input id="au-pass" type="password" autocomplete="${newpw ? 'new-password' : 'current-password'}"></label>
    ${newpw ? '<label class="full">Confirm password<input id="au-pass2" type="password" autocomplete="new-password"></label>' : ''}</div>
    <div class="row" style="margin-top:16px"><button class="primary" style="flex:1" data-a="authgo" data-mode="${mode}">${setup ? 'Create admin account' : reg ? 'Join family' : fam ? 'Create my family' : 'Sign in'}</button></div>
    ${links}</div>`;
  const u = $(reg && !code ? '#au-code' : '#au-user'); if (u) u.focus();
}
async function doAuth(mode) {
  const username = $('#au-user').value, password = $('#au-pass').value;
  if (mode !== 'login' && password !== $('#au-pass2').value) return toast('Passwords do not match');
  const url = { setup: '/api/auth/setup', register: '/api/auth/register', signup: '/api/auth/signup', login: '/api/auth/login' }[mode];
  const body = { username, password };
  if (mode === 'register') body.code = $('#au-code').value;
  const r = await api('POST', url, body);
  if (mode === 'register') history.replaceState(null, '', location.pathname);
  S.user = r.user; S.boot = null; await startApp();
}
const TABS = [['plan', 'Weekly Plan', 'Plan', 'plan'], ['grocery', 'Groceries', 'Groceries', 'cart'], ['recipes', 'Recipes', 'Recipes', 'book'],
  ['household', 'Household', 'Family', 'users'], ['prices', 'Price Book', 'Prices', 'tag'], ['settings', 'Settings', 'Settings', 'gear']];
async function show(tab) {
  S.tab = tab;
  $('#nav').innerHTML = TABS.map(([k, l, s, i]) => `<button class="${k === tab ? 'on' : ''}" data-a="tab" data-k="${k}" aria-label="${l}">${ic(i)}<span class="lg">${l}</span><span class="sh">${s}</span></button>`).join('');
  window.scrollTo(0, 0);
  await guard(async () => {
    if (tab === 'plan') await viewPlan();
    else if (tab === 'grocery') await viewGrocery();
    else if (tab === 'recipes') viewRecipes();
    else if (tab === 'household') viewHousehold();
    else if (tab === 'prices') viewPrices();
    else await viewSettings();
  });
}

/* ---------------------------------------------------------------- plan */
function gridTable(grid, scope) {
  return `<table class="grid-tbl"><tr><th></th>${DAYS.map((d, i) => `<th>${d}</th>`).join('')}</tr>` +
    SLOTS.map(s => `<tr><th>${cap(s)}</th>${DAYS.map((_, d) => `<td><input type="checkbox" data-c="grid" data-scope="${scope}" data-day="${d}" data-slot="${s}" ${grid[d][s] ? 'checked' : ''}></td>`).join('')}</tr>`).join('') +
    `</table>`;
}
async function loadPlan() { S.plan = await api('GET', '/api/week/' + S.ws); }

async function viewPlan() {
  await loadPlan();
  const p = S.plan, t = p.totals, w = p.week;
  const pct = t.budget > 0 ? Math.min(100, t.groceries / t.budget * 100) : 100;
  const cls = t.groceries > t.budget ? 'over' : t.groceries > t.budget * 0.9 ? 'warn' : '';
  const today = fmtISO(new Date());
  const byCell = {}; p.entries.forEach(e => byCell[e.day + e.slot] = e);
  const eatingIds = S.people.filter(x => x.active);
  $('#main').innerHTML = `
  <div class="weekbar">
    <button data-a="wk" data-n="-7">‹</button>
    <h2>Week of ${longDate(S.ws)} – ${longDate(addDays(S.ws, 6))}</h2>
    <button data-a="wk" data-n="0">This week</button>
    <button data-a="wk" data-n="7">›</button>
    <button data-a="copyprev">Copy last week</button>
  </div>
  <div class="card">
    <div class="budgetrow">
      <label>Weekly grocery budget<input type="number" min="0" step="5" value="${w.budget}" data-c="budget"></label>
      <div><div class="muted small">Estimated groceries</div><div class="stat">${money(t.groceries)}</div></div>
      <div><div class="muted small">${t.remaining >= 0 ? 'Left in budget' : 'Over budget by'}</div><div class="stat" style="color:var(--${t.remaining >= 0 ? 'accent' : 'bad'})">${money(Math.abs(t.remaining))}</div></div>
      <div><div class="muted small">Feeding</div><div class="stat">${num(p.headcount)} <span class="small muted">portions</span></div></div>
    </div>
    <div class="bar ${cls}"><div style="width:${pct}%"></div></div>
    <div class="row">
      <button class="primary" data-a="gen" data-mode="fill">Fill empty meals</button>
      <button data-a="gen" data-mode="all">Regenerate unlocked</button>
      <button class="danger" data-a="clear">Clear unlocked</button>
      <span class="muted small">Prices are estimates from your Price Book. Items marked “in pantry” are not counted.</span>
    </div>
  </div>
  <details class="card" id="wkset" ${S.wkOpen ? 'open' : ''}>
    <summary><b>This week’s settings</b> <span class="muted small">— who’s eating, guests, which meals to plan</span></summary>
    <div class="row" style="margin:12px 0">
      <div><div class="muted small">Who’s eating this week</div>
        <div class="checks">${eatingIds.map(x => `<label class="inline"><input type="checkbox" data-c="away" data-id="${x.id}" ${w.away.includes(x.id) ? '' : 'checked'}>${esc(x.name)}</label>`).join('') || '<span class="muted small">Add people in the Household tab.</span>'}</div></div>
      <label>Extra guests<input type="number" min="0" step="1" style="width:80px" value="${w.guests}" data-c="guests"></label>
      <label class="inline"><input type="checkbox" data-c="quick" ${w.quick ? 'checked' : ''}>Prefer quick (≤30 min) dinners Mon–Thu</label>
    </div>
    <div class="muted small">Meals to plan this week (uncheck anything you’re eating out or skipping):</div>
    ${gridTable(w.grid, 'wk')}
  </details>
  <div class="plan">${DAYS.map((d, i) => `
    <div class="day ${p.dates[i] === today ? 'today' : ''}"><h4>${d} ${longDate(p.dates[i])}</h4>
    ${SLOTS.filter(s => w.grid[i][s]).map(s => {
      const e = byCell[i + s];
      if (!e) return `<button class="empty" data-a="pick" data-day="${i}" data-slot="${s}">+ ${cap(s)}</button>`;
      const r = e.recipe;
      return `<div class="meal ${s} ${e.locked ? 'locked' : ''}"><div class="slot">${s}</div>
        <a data-a="view" data-id="${r.id}">${esc(r.name)}</a>
        <div class="muted small">${money(r.cost)} · ${r.minutes} min</div>
        <div class="acts">
          <button class="ghost ${e.locked ? 'active' : ''}" title="${e.locked ? 'Unlock' : 'Lock so regenerate keeps it'}" data-a="lock" data-day="${i}" data-slot="${s}">${ic(e.locked ? 'lock' : 'unlock')}</button>
          <button class="ghost" title="Swap" data-a="pick" data-day="${i}" data-slot="${s}">${ic('swap')}</button>
          <button class="ghost ${r.feedback > 0 ? 'active' : ''}" title="We like this" data-a="fb" data-id="${r.id}" data-v="${r.feedback > 0 ? 0 : 1}">${ic('up')}</button>
          <button class="ghost ${r.feedback < 0 ? 'active' : ''}" title="Not great" data-a="fb" data-id="${r.id}" data-v="${r.feedback < 0 ? 0 : -1}">${ic('up', 'flip')}</button>
          <button class="ghost" title="Remove" data-a="rm" data-day="${i}" data-slot="${s}">${ic('x')}</button>
        </div></div>`;
    }).join('') || '<div class="muted small">No meals planned</div>'}
    </div>`).join('')}</div>`;
}

async function saveWeek(patch) {
  const w = S.plan.week;
  S.plan = await api('PUT', '/api/week/' + S.ws, Object.assign({ budget: w.budget, grid: w.grid, away: w.away, guests: w.guests, quick: w.quick }, patch));
  await viewPlan();
}
async function planAction(path, body) {
  S.plan = await api('POST', '/api/week/' + S.ws + path, body || {});
  await viewPlan();
}

async function openPick(day, slot) {
  const sugg = await api('GET', `/api/week/${S.ws}/suggest?day=${day}&slot=${slot}`);
  S.pickCtx = { day, slot, sugg };
  S.pq = '';
  openModal(`<div class="row spread"><h3>Pick ${slot} for ${DAYS[day]}</h3><button class="ghost" data-a="close">✕</button></div>
    <input id="pickq" placeholder="Search all ${slot} recipes…" data-i="pickq" style="width:100%;margin-bottom:8px">
    <div id="pick-results">${suggRows(sugg, true)}</div>`);
}
function suggRows(list, ranked) {
  if (!list.length) return '<p class="muted">No matching recipes. Add some in the Recipes tab.</p>';
  return (ranked ? '<div class="muted small">Best matches for your household and budget</div>' : '') + list.map(r => `
    <div class="sugg"><div style="flex:1"><b>${esc(r.name)}</b>
      <div class="muted small">${money(r.cost)} · ${r.minutes} min${r.why && r.why.length ? ' · ' + esc(r.why.join(', ')) : ''}</div></div>
      <button class="primary" data-a="choose" data-id="${r.id}">Choose</button></div>`).join('');
}
function pickSearch(q) {
  const { day, slot, sugg } = S.pickCtx;
  q = q.trim().toLowerCase();
  if (!q) { $('#pick-results').innerHTML = suggRows(sugg, true); return; }
  const hc = S.plan.headcount;
  const list = S.recipes.filter(r => r.slots.includes(slot) && (r.name.toLowerCase().includes(q) || r.names.some(n => n.includes(q))))
    .map(r => ({ id: r.id, name: r.name, minutes: r.minutes, cost: r.cost_per_serving * hc, why: [] }));
  $('#pick-results').innerHTML = suggRows(list, false);
}

function viewRecipeModal(id) {
  const r = S.recipes.find(x => x.id === id); if (!r) return;
  const hc = S.plan ? S.plan.headcount : r.servings, mult = hc / r.servings;
  const pb = Object.fromEntries(S.pb.map(p => [p.name, p]));
  openModal(`<div class="row spread"><h3>${esc(r.name)}</h3><button class="ghost" data-a="close">✕</button></div>
    <div class="muted small">${r.minutes} min · ${money(r.cost_per_serving)}/serving · scaled for ${num(hc)} portions (recipe makes ${r.servings})</div>
    <div style="margin:8px 0">${r.tags.map(t => `<span class="tag">${t}</span>`).join('')}</div>
    <h3>Ingredients</h3><ul>${r.ingredients.map(i => `<li>${num(i.q * mult)} ${esc((pb[i.n] || {}).unit || '')} ${esc(i.n)}</li>`).join('')}</ul>
    <h3>Steps</h3><div class="steps">${esc(r.steps) || '<span class="muted">No steps yet.</span>'}</div>
    <p class="muted small">Assumes pantry staples: salt, pepper, common spices.</p>`);
}

/* ---------------------------------------------------------------- groceries */
const meijerUrl = q => 'https://www.meijer.com/shopping/search.html?text=' + encodeURIComponent(q);
function shopItems() {
  const g = S.gl; if (!g) return [];
  const out = [];
  g.aisles.forEach(a => a.items.forEach(i => out.push({ name: i.name, qty: num(i.qty) + ' ' + i.unit, checked: i.checked })));
  g.extras.forEach(x => out.push({ name: x.name, qty: '', checked: false, extra: true }));
  return out;
}
function listText(onlyOpen) {
  return shopItems().filter(i => !(onlyOpen && i.checked)).map(i => '• ' + cap(i.name) + (i.qty ? ' — ' + i.qty : '')).join('\n');
}
async function copyText(t) {
  try { if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(t); return true; } } catch (e) { /* fall through */ }
  const ta = document.createElement('textarea'); ta.value = t; ta.style.cssText = 'position:fixed;opacity:0;top:0';
  document.body.appendChild(ta); ta.select();
  let ok = false; try { ok = document.execCommand('copy'); } catch (e) { /* ignore */ }
  ta.remove(); return ok;
}
function renderShop() {
  const items = S.shop.items, n = items.length;
  if (S.shop.i >= n) {
    return openModal(`<div class="row spread"><h3>All done</h3><button class="ghost" data-a="close" aria-label="Close">${ic('x')}</button></div>
      <p>You went through all ${n} items. Open your Meijer cart to check out.</p>
      <div class="row"><a class="btn primary" href="https://www.meijer.com/shopping/cart.html" target="_blank" rel="noopener noreferrer">Open Meijer cart</a><button data-a="shopback">Back</button><button data-a="close">Close</button></div>`);
  }
  const it = items[S.shop.i];
  openModal(`<div class="row spread"><h3>Shop at Meijer <span class="muted small">${S.shop.i + 1} of ${n}</span></h3><button class="ghost" data-a="close" aria-label="Close">${ic('x')}</button></div>
    <div class="bar"><div style="width:${S.shop.i / n * 100}%"></div></div>
    <div class="shopitem"><div class="shopname">${esc(cap(it.name))}</div><div class="muted">${it.qty ? 'Need ' + esc(it.qty) : 'Extra item'}</div></div>
    <a class="btn primary big" href="${meijerUrl(it.name)}" target="_blank" rel="noopener noreferrer">${ic('ext')} Find in Meijer</a>
    <div class="row shopnav"><button data-a="shopback" ${S.shop.i ? '' : 'disabled'}>‹ Back</button>
      <button data-a="shopskip">Skip</button><button class="primary" data-a="shopdone">${ic('cart')} Added — next</button></div>
    <p class="muted small" style="margin-bottom:0">Meijer doesn’t let other apps fill your cart, so this opens each item in Meijer. Tap “Add to cart” there, return here, then tap “Added — next”.</p>`);
}
async function viewGrocery() {
  const g = await api('GET', `/api/week/${S.ws}/grocery`);
  S.gl = g;
  const row = i => `<div class="list-item ${i.checked ? 'done' : ''}">
      <input type="checkbox" data-c="gcheck" data-name="${esc(i.name)}" ${i.checked ? 'checked' : ''}>
      <div class="nm">${esc(cap(i.name))} <span class="muted small">— ${num(i.qty)} ${esc(i.unit)}</span>
      <div class="muted small">${esc(i.meals.join(', '))}</div></div><div>${money(i.cost)}</div>
      <a class="ghost lnk noprint" href="${meijerUrl(i.name)}" target="_blank" rel="noopener noreferrer" aria-label="Find ${esc(i.name)} in Meijer">${ic('ext')}</a></div>`;
  $('#main').innerHTML = `
  <div class="weekbar"><button data-a="gwk" data-n="-7">‹</button><h2>Groceries — week of ${longDate(S.ws)}</h2>
    <button data-a="gwk" data-n="7">›</button><button class="noprint" data-a="print">Print</button></div>
  <div class="card"><div class="row spread"><div><div class="muted small">Estimated total</div><div class="stat">${money(g.total)}</div></div>
    <div class="muted">Budget ${money(g.budget)} · ${g.total <= g.budget ? money(g.budget - g.total) + ' left' : money(g.total - g.budget) + ' over'}</div></div></div>
  ${g.meal_count === 0 ? '<div class="card muted">No meals planned for this week yet. Head to the Weekly Plan tab.</div>' : `<div class="card noprint"><div class="row spread"><div><h3 style="margin:0">Shopping at Meijer</h3><div class="muted small">Step through your list, one item at a time.</div></div></div>
    <div class="row" style="margin-top:10px"><button class="primary" data-a="shopstart">${ic('cart')} Start shopping</button><button data-a="listcopy">Copy list</button>${navigator.share ? '<button data-a="listshare">Share</button>' : ''}</div></div>`}
  <div class="cols">${g.aisles.map(a => `<div class="card"><h3>${esc(a.name)}</h3>${a.items.map(row).join('')}</div>`).join('')}
    <div class="card"><h3>Extras</h3>${g.extras.map(x => `<div class="list-item"><div class="nm">${esc(x.name)}</div><div>${money(x.price)}</div><button class="ghost" data-a="xdel" data-id="${x.id}">✕</button></div>`).join('')}
      <div class="row noprint" style="margin-top:8px"><input id="xname" placeholder="Add an item" style="flex:1"><input id="xprice" type="number" step="0.25" min="0" placeholder="Price" style="width:80px"><button data-a="xadd">Add</button></div></div>
    ${g.pantry.length ? `<div class="card"><h3>Already in pantry</h3>${g.pantry.map(i => `<div class="list-item"><div class="nm muted">${esc(cap(i.name))} — ${num(i.qty)} ${esc(i.unit)}</div></div>`).join('')}</div>` : ''}
  </div>`;
}

/* ---------------------------------------------------------------- recipes */
function viewRecipes() {
  const f = S.rfilter;
  const list = S.recipes.filter(r => (!f.slot || r.slots.includes(f.slot)) && (!f.q || r.name.toLowerCase().includes(f.q.toLowerCase()) || r.names.some(n => n.includes(f.q.toLowerCase()))));
  $('#main').innerHTML = `
  <div class="row spread" style="margin-bottom:12px"><h2 style="margin:0">Recipes <span class="muted small">${list.length} of ${S.recipes.length}</span></h2>
    <div class="row"><button data-a="rimport">Import from URL</button><button class="primary" data-a="rnew">+ New recipe</button></div></div>
  <div class="row" style="margin-bottom:12px">
    <input placeholder="Search recipes or ingredients…" value="${esc(f.q)}" data-i="rq" style="min-width:240px">
    <button class="chip ${!f.slot ? 'on' : ''}" data-a="rslot" data-v="">All</button>
    ${SLOTS.map(s => `<button class="chip ${f.slot === s ? 'on' : ''}" data-a="rslot" data-v="${s}">${cap(s)}</button>`).join('')}</div>
  <div class="cols" id="rlist">${list.map(rcard).join('') || '<p class="muted">No recipes match.</p>'}</div>`;
}
const rcard = r => `<div class="card"><div class="row spread"><b>${esc(r.name)}</b><span class="muted small">${money(r.cost_per_serving)}/serving</span></div>
  <div class="muted small">${r.slots.map(cap).join(', ')} · ${r.minutes} min · serves ${r.servings}</div>
  <div style="margin:6px 0">${r.tags.map(t => `<span class="tag">${t}</span>`).join('')}</div>
  <div class="row"><button data-a="view" data-id="${r.id}">View</button><button data-a="redit" data-id="${r.id}">Edit</button><button class="danger" data-a="rdel" data-id="${r.id}">Delete</button></div></div>`;

function ingRow(i, unit) {
  return `<div class="row ing ${i.matched === false ? 'unmatched' : ''}" title="${esc(i.raw || '')}" style="margin-bottom:6px"><input list="pbnames" placeholder="ingredient" value="${esc(i.n || '')}" data-i="ingname" style="flex:2;min-width:140px">
    <input type="number" step="0.25" min="0" placeholder="qty" value="${i.q ?? ''}" style="width:80px"><input placeholder="unit" value="${esc(unit || '')}" style="width:80px">
    <button class="ghost" data-a="ingdel">✕</button></div>`;
}
function openRecipeForm(id, draft) {
  const r = id ? S.recipes.find(x => x.id === id)
    : draft ? { name: draft.name, slots: ['dinner'], minutes: draft.minutes, servings: draft.servings, steps: draft.steps, ingredients: draft.ingredients }
    : { name: '', slots: ['dinner'], minutes: 30, servings: 4, steps: '', ingredients: [{}] };
  const pb = Object.fromEntries(S.pb.map(p => [p.name, p]));
  S.editRecipe = id || null;
  openModal(`<div class="row spread"><h3>${id ? 'Edit' : draft ? 'Review imported' : 'New'} recipe</h3><button class="ghost" data-a="close">✕</button></div>
  ${draft ? `<p class="muted small">Check each ingredient before saving. Rows outlined in amber weren’t found in your Price Book — pick a matching item or they’ll be added at $0. Quantities are converted to the Price Book unit where possible.</p>` : ''}
  <datalist id="pbnames">${S.pb.map(p => `<option value="${esc(p.name)}">`).join('')}</datalist>
  <div class="formgrid">
    <label class="full">Name<input id="rf-name" value="${esc(r.name)}"></label>
    <div class="full"><div class="muted small">Good for</div><div class="checks">${SLOTS.map(s => `<label class="inline"><input type="checkbox" class="rf-slot" value="${s}" ${r.slots.includes(s) ? 'checked' : ''}>${cap(s)}</label>`).join('')}</div></div>
    <label>Minutes<input id="rf-min" type="number" min="1" value="${r.minutes}"></label>
    <label>Makes (servings)<input id="rf-serv" type="number" min="1" value="${r.servings}"></label>
    <div class="full"><div class="muted small" style="margin-bottom:4px">Ingredients for the full recipe (unit comes from the Price Book; new ingredients are added there at $0 — set a price later)</div>
      <div id="rf-ings">${r.ingredients.map(i => ingRow(i, (pb[i.n] || {}).unit || i.unit)).join('')}</div>
      <button data-a="ingadd">+ Add ingredient</button></div>
    <label class="full">Steps<textarea id="rf-steps">${esc(r.steps)}</textarea></label>
  </div>
  <div class="row" style="margin-top:12px"><button class="primary" data-a="rsave">Save recipe</button><button data-a="close">Cancel</button></div>`, true);
}
async function saveRecipe() {
  const ings = [...document.querySelectorAll('#rf-ings .ing')].map(row => {
    const [n, q, u] = row.querySelectorAll('input'); return { n: n.value, q: q.value, unit: u.value };
  });
  const body = { name: $('#rf-name').value, slots: [...document.querySelectorAll('.rf-slot:checked')].map(x => x.value),
    minutes: $('#rf-min').value, servings: $('#rf-serv').value, steps: $('#rf-steps').value, ingredients: ings };
  await guard(async () => {
    await api(S.editRecipe ? 'PUT' : 'POST', '/api/recipes' + (S.editRecipe ? '/' + S.editRecipe : ''), body);
    [S.recipes, S.pb] = await Promise.all([api('GET', '/api/recipes'), api('GET', '/api/pricebook')]);
    closeModal(); viewRecipes(); toast('Recipe saved');
  });
}

/* ---------------------------------------------------------------- household */
function viewHousehold() {
  $('#main').innerHTML = `<div class="row spread" style="margin-bottom:12px"><h2 style="margin:0">Household</h2><button class="primary" data-a="pnew">+ Add person</button></div>
  <p class="muted">Diets and allergies are hard rules for meal suggestions. Likes and dislikes (comma-separated ingredients or dishes) nudge suggestions. Use “Who’s eating” on the Weekly Plan to skip someone for a week.</p>
  <div class="cols">${S.people.map(p => `<div class="card ${p.active ? '' : 'muted'}"><div class="row spread"><b>${esc(p.name)}</b><span class="muted small">${p.kind} · ${p.portion} portion${p.active ? '' : ' · inactive'}</span></div>
    <div style="margin:6px 0">${[...p.diets, ...p.allergies.map(a => a + ' allergy')].map(t => `<span class="tag">${esc(t)}</span>`).join('')}${p.allergy_text ? `<span class="tag">avoid: ${esc(p.allergy_text)}</span>` : ''}</div>
    ${p.likes ? `<div class="small">Likes: ${esc(p.likes)}</div>` : ''}${p.dislikes ? `<div class="small">Dislikes: ${esc(p.dislikes)}</div>` : ''}
    <div class="row" style="margin-top:8px"><button data-a="pedit" data-id="${p.id}">Edit</button><button class="danger" data-a="pdel" data-id="${p.id}">Remove</button></div></div>`).join('') || '<div class="card muted">No one added yet.</div>'}</div>`;
}
function personForm(p, idPrefix) {
  const chk = (list, vals, cls) => list.map(v => `<label class="inline"><input type="checkbox" class="${cls}" value="${v}" ${vals.includes(v) ? 'checked' : ''}>${v}</label>`).join('');
  return `<div class="formgrid">
    <label>Name<input id="${idPrefix}name" value="${esc(p.name)}"></label>
    <label>Type<select id="${idPrefix}kind"><option value="adult" ${p.kind === 'adult' ? 'selected' : ''}>Adult / teen</option><option value="child" ${p.kind === 'child' ? 'selected' : ''}>Child</option></select></label>
    <label>Portion size (1 = adult)<input id="${idPrefix}portion" type="number" step="0.1" min="0.1" max="3" value="${p.portion}"></label>
    <label class="inline" style="align-self:end"><input type="checkbox" id="${idPrefix}active" ${p.active ? 'checked' : ''}>Active in household</label>
    <div class="full"><div class="muted small">Diet</div><div class="checks">${chk(S.boot.diets, p.diets, idPrefix + 'diet')}</div></div>
    <div class="full"><div class="muted small">Allergies / avoid</div><div class="checks">${chk(S.boot.allergens, p.allergies, idPrefix + 'allergy')}</div></div>
    <label class="full">Other ingredients to never use (comma-separated)<input id="${idPrefix}atext" value="${esc(p.allergy_text)}" placeholder="e.g. mushrooms, cilantro"></label>
    <label class="full">Likes<input id="${idPrefix}likes" value="${esc(p.likes)}" placeholder="e.g. tacos, chicken, pasta"></label>
    <label class="full">Dislikes<input id="${idPrefix}dislikes" value="${esc(p.dislikes)}" placeholder="e.g. spinach, tofu"></label></div>`;
}
const readPerson = pre => ({
  name: $('#' + pre + 'name').value, kind: $('#' + pre + 'kind').value, portion: $('#' + pre + 'portion').value, active: $('#' + pre + 'active').checked,
  diets: [...document.querySelectorAll('.' + pre + 'diet:checked')].map(x => x.value),
  allergies: [...document.querySelectorAll('.' + pre + 'allergy:checked')].map(x => x.value),
  allergy_text: $('#' + pre + 'atext').value, likes: $('#' + pre + 'likes').value, dislikes: $('#' + pre + 'dislikes').value });
function openPersonForm(id) {
  const p = id ? S.people.find(x => x.id === id) : { name: '', kind: 'adult', portion: 1, diets: [], allergies: [], allergy_text: '', likes: '', dislikes: '', active: true };
  S.editPerson = id || null;
  openModal(`<div class="row spread"><h3>${id ? 'Edit' : 'Add'} person</h3><button class="ghost" data-a="close">✕</button></div>${personForm(p, 'pf-')}
  <div class="row" style="margin-top:12px"><button class="primary" data-a="psave">Save</button><button data-a="close">Cancel</button></div>`, true);
}

/* ---------------------------------------------------------------- price book */
function viewPrices() {
  const q = S.pq.toLowerCase();
  const list = S.pb.filter(p => !q || p.name.includes(q) || p.aisle.toLowerCase().includes(q));
  $('#main').innerHTML = `<h2>Price Book</h2>
  <p class="muted">Your own price list for ${esc(S.boot.settings.store_name)} — starter values are rough estimates, so update them from your receipts. Recipe quantities use each item’s unit. Tick “Have” for pantry items you already own and they’ll drop out of the grocery cost.</p>
  <div class="card"><div class="row"><input placeholder="Search items or aisles…" value="${esc(S.pq)}" data-i="pq" style="min-width:240px">
    <button data-a="pbadd">+ Add item</button><button data-a="pbimport">Import CSV</button></div></div>
  <div class="card" style="overflow:auto"><table class="pb-table"><tr><th>Item</th><th>Unit</th><th>Price / unit</th><th>Aisle</th><th>Flags</th><th>Have</th><th></th></tr>
  ${list.map(p => `<tr data-name="${esc(p.name)}"><td>${esc(p.name)}</td><td style="width:90px"><input value="${esc(p.unit)}" data-c="pbf" data-k="unit"></td>
    <td style="width:100px"><input type="number" step="0.01" min="0" value="${p.price}" data-c="pbf" data-k="price"></td>
    <td style="width:150px"><select data-c="pbf" data-k="aisle">${[...new Set([...AISLES, p.aisle])].map(a => `<option ${a === p.aisle ? 'selected' : ''}>${esc(a)}</option>`).join('')}</select></td>
    <td style="width:230px"><input value="${esc(p.flags)}" data-c="pbf" data-k="flags" placeholder="e.g. dairy gluten"></td>
    <td><input type="checkbox" data-c="pbf" data-k="on_hand" ${p.on_hand ? 'checked' : ''}></td>
    <td><button class="ghost" data-a="pbdel" data-name="${esc(p.name)}">✕</button></td></tr>`).join('')}</table></div>
  <p class="muted small">Flags (space-separated) drive diet &amp; allergy filtering: ${FLAGS.join(', ')}.</p>`;
}
function openPbForm() {
  openModal(`<div class="row spread"><h3>Add price book item</h3><button class="ghost" data-a="close">✕</button></div><div class="formgrid">
    <label>Name<input id="nb-name"></label><label>Unit<input id="nb-unit" value="each"></label>
    <label>Price per unit<input id="nb-price" type="number" step="0.01" min="0" value="0"></label>
    <label>Aisle<select id="nb-aisle">${AISLES.map(a => `<option>${a}</option>`).join('')}</select></label>
    <label class="full">Flags (space-separated: ${FLAGS.join(' ')})<input id="nb-flags"></label></div>
    <div class="row" style="margin-top:12px"><button class="primary" data-a="pbsave">Save</button><button data-a="close">Cancel</button></div>`);
}
function openImport() {
  openModal(`<div class="row spread"><h3>Import prices</h3><button class="ghost" data-a="close">✕</button></div>
    <p class="muted small">One item per line: <code>name,unit,price[,aisle]</code>. Existing items get their unit and price updated; new ones are added.</p>
    <textarea id="csv" placeholder="chicken breast,lb,3.29&#10;milk,cup,0.22"></textarea>
    <div class="row" style="margin-top:12px"><button class="primary" data-a="csvgo">Import</button><button data-a="close">Cancel</button></div>`);
}

/* ---------------------------------------------------------------- settings */
async function viewSettings() {
  const s = S.boot.settings, admin = S.user && S.user.role === 'admin';
  const users = admin ? await api('GET', '/api/users') : [];
  const invites = admin ? await api('GET', '/api/invites') : [];
  const site = S.user.superadmin ? await api('GET', '/api/site') : null;
  const theme = document.documentElement.dataset.theme;
  $('#main').innerHTML = `<h2>Settings</h2>
  ${admin ? `<div class="card"><h3>Household</h3><div class="formgrid">
    <label>Household name<input id="st-name" value="${esc(s.household_name)}"></label>
    <label>Store name<input id="st-store" value="${esc(s.store_name)}"></label>
    <label>Currency symbol<input id="st-cur" value="${esc(s.currency)}" style="width:80px"></label></div>
    <div class="row" style="margin-top:12px"><button class="primary" data-a="stsave">Save</button></div></div>` : ''}
  <div class="card"><h3>Appearance</h3><label style="max-width:200px">Theme<select data-c="theme"><option value="dark" ${theme === 'dark' ? 'selected' : ''}>Dark</option><option value="light" ${theme === 'light' ? 'selected' : ''}>Light</option></select></label></div>
  <div class="card"><h3>My account</h3><p class="muted small" style="margin-top:0">Signed in as <b>${esc(S.user.username)}</b> <span class="badge ${S.user.role}">${S.user.role}</span></p>
    <div class="formgrid"><label>Current password<input id="pw-cur" type="password" autocomplete="current-password"></label>
    <label>New password (8+ characters)<input id="pw-new" type="password" autocomplete="new-password"></label></div>
    <div class="row" style="margin-top:12px"><button data-a="pwsave">Change password</button></div></div>
  ${admin ? `<div class="card"><h3>Invite family</h3><p class="muted small" style="margin-top:0">Create a one-time invite link for someone to join <b>your</b> family. They pick their own username and password and join as a member, sharing your plans. Invites expire after 7 days.</p>
    ${invites.map(i => `<div class="list-item"><div class="nm muted">Pending invite · expires ${new Date(i.expires * 1000).toLocaleDateString()}</div><button class="danger" data-a="invdel" data-id="${i.id}">Revoke</button></div>`).join('')}
    <div class="row" style="margin-top:10px"><button class="primary" data-a="invnew">Create invite</button></div></div>
  <div class="card"><h3>Accounts</h3><p class="muted small" style="margin-top:0">Members can plan meals, edit recipes and use the grocery list; only admins manage accounts and settings. You can also add someone directly below.</p>
    ${users.map(u => `<div class="list-item"><div class="nm"><b>${esc(u.username)}</b> <span class="badge ${u.role}">${u.role}</span></div>
      <button data-a="upw" data-id="${u.id}" data-name="${esc(u.username)}">Reset password</button>
      <button data-a="urole" data-id="${u.id}" data-role="${u.role === 'admin' ? 'member' : 'admin'}">Make ${u.role === 'admin' ? 'member' : 'admin'}</button>
      ${u.id === S.user.id ? '' : `<button class="danger" data-a="udel" data-id="${u.id}" data-name="${esc(u.username)}">Remove</button>`}</div>`).join('')}
    <div class="formgrid" style="margin-top:14px"><label>New username<input id="nu-name" autocapitalize="none" spellcheck="false"></label>
    <label>Password (8+ characters)<input id="nu-pass" type="password" autocomplete="new-password"></label>
    <label class="inline"><input type="checkbox" id="nu-admin">Admin</label></div>
    <div class="row" style="margin-top:12px"><button class="primary" data-a="uadd">Add account</button></div></div>` : ''}
  ${site ? `<div class="card"><h3>Site owner: families on this server</h3>
    <p class="muted small" style="margin-top:0">Each family has its own private plans, recipes, prices and people. Nobody can see another family’s data, not even their admins. Invites only ever add people to the inviter’s own family.</p>
    <label class="inline"><input type="checkbox" data-c="signupopen" ${site.signup_open ? 'checked' : ''}>Let anyone create their own new family on this server (shows a “Create your own family” link on the sign-in page)</label>
    <div style="margin-top:10px">${site.households.map(h => `<div class="list-item"><div class="nm"><b>Family #${h.id}</b>${h.own ? ' <span class="badge admin">yours</span>' : ''}<div class="muted small">${h.members.map(esc).join(', ') || 'no members'}</div></div>
      ${h.own ? '' : `<button class="danger" data-a="hdel" data-id="${h.id}" data-name="${esc(h.members.join(', '))}">Delete family</button>`}</div>`).join('')}</div>
    <p class="muted small">${site.households.length} of ${site.max} families.</p></div>` : ''}
  ${admin ? `<div class="card"><h3>Backup</h3><p class="muted">Everything lives in a single SQLite file on the server (<code>data/plateful.db</code>). Back it up with Proxmox container backups, or download a JSON export of your plans and recipes (accounts are never exported):</p>
    <a class="btn" href="/api/export" download="plateful-export.json">Download JSON export</a></div>` : ''}
  <div class="card"><h3>How suggestions work</h3><p class="muted">Everything runs on this server — nothing is sent to the internet. Each meal is scored by your family’s likes/dislikes, thumbs up/down, how recently you ate it, ingredient overlap with other meals (less waste), and how it fits your budget. Diets and allergies are hard filters. Unlock or lock meals, then regenerate to reshuffle the rest.</p></div>`;
}

/* ---------------------------------------------------------------- wizard */
function startWizard() {
  const g = {}; for (let d = 0; d < 7; d++) { g[d] = {}; SLOTS.forEach(s => g[d][s] = s === 'dinner'); }
  S.wiz = { step: 0, household_name: '', store_name: S.boot.settings.store_name === 'My Grocery Store' ? '' : S.boot.settings.store_name, budget: 150, grid: g, people: [blankPerson()] };
  $('#nav').innerHTML = ''; renderWizard();
}
const blankPerson = () => ({ name: '', kind: 'adult', portion: 1, diets: [], allergies: [], allergy_text: '', likes: '', dislikes: '', active: true });
function renderWizard() {
  const w = S.wiz, steps = ['Welcome', 'Your people', 'Budget & meals', 'Ready'];
  let body = '';
  if (w.step === 0) body = `<h2>Welcome to Plateful</h2><p class="muted">A private meal planner that runs on your own server. Everything here can be changed later, week by week. Let’s set up the basics.</p>
    <div class="formgrid"><label class="full">Household name<input data-i="wz" data-k="household_name" value="${esc(w.household_name)}" placeholder="The Smith Family"></label>
    <label class="full">Where do you shop?<input data-i="wz" data-k="store_name" value="${esc(w.store_name)}" placeholder="Meijer"></label></div>`;
  else if (w.step === 1) body = `<h2>Who are you feeding?</h2><p class="muted">Add everyone who normally eats. You can add or remove people anytime, and skip someone for a week.</p>
    ${w.people.map((p, i) => `<div class="card" data-idx="${i}"><div class="row spread"><b>Person ${i + 1}</b>${w.people.length > 1 ? `<button class="ghost" data-a="wzpdel" data-idx="${i}">Remove</button>` : ''}</div>${personForm(p, 'wp' + i + '-')}</div>`).join('')}
    <button data-a="wzpadd">+ Add another person</button>`;
  else if (w.step === 2) body = `<h2>Budget &amp; meals</h2><label style="max-width:220px">Weekly grocery budget<input type="number" min="0" step="5" data-i="wz" data-k="budget" value="${w.budget}"></label>
    <p class="muted">Which meals do you want planned? Change this any week from the Weekly Plan tab.</p>
    <div class="row" style="margin-bottom:8px"><button data-a="wzpreset" data-v="d">Dinners only</button><button data-a="wzpreset" data-v="ld">Lunch + dinner</button><button data-a="wzpreset" data-v="all">All meals</button><button data-a="wzpreset" data-v="allsn">Everything + snacks</button></div>
    ${gridTable(w.grid, 'wz')}`;
  else body = `<h2>You’re all set</h2><p>${esc(w.household_name || 'Your household')} · ${w.people.filter(p => p.name.trim()).length} people · ${money(w.budget)}/week budget.</p>
    <p class="muted">Next: open the Weekly Plan and press “Fill empty meals”. Tip: the Price Book has starter estimates — update prices from your receipts for better budgeting.</p>`;
  $('#main').innerHTML = `<div class="wiz"><div class="dots">${steps.map((_, i) => `<i class="${i <= w.step ? 'on' : ''}"></i>`).join('')}</div><div class="card">${body}
    <div class="row spread" style="margin-top:16px">${w.step > 0 ? '<button data-a="wzback">Back</button>' : '<span></span>'}
    ${w.step < 3 ? '<button class="primary" data-a="wznext">Next</button>' : '<button class="primary" data-a="wzfinish">Start planning</button>'}</div></div></div>`;
}
function wizSyncPeople() {
  S.wiz.people = S.wiz.people.map((p, i) => document.getElementById('wp' + i + '-name') ? Object.assign(readPerson('wp' + i + '-')) : p);
}
async function wizFinish() {
  const w = S.wiz;
  await guard(async () => {
    await api('POST', '/api/setup', { household_name: w.household_name, store_name: w.store_name, budget: w.budget, grid: w.grid,
      people: w.people.filter(p => p.name.trim()), week: S.ws || S.boot.this_week });
    S.wiz = null; await loadBase(); await show('plan');
  });
}

/* ---------------------------------------------------------------- events */
document.addEventListener('click', e => {
  const el = e.target.closest('[data-a]'); if (!el) return;
  const a = el.dataset.a, d = el.dataset;
  if (a === 'overlay') { if (e.target === el) closeModal(); return; }
  e.preventDefault();
  guard(async () => {
    switch (a) {
      case 'close': return closeModal();
      case 'tab': return show(d.k);
      case 'wk': S.ws = d.n === '0' ? S.boot.this_week : addDays(S.ws, +d.n); return viewPlan();
      case 'gwk': S.ws = addDays(S.ws, +d.n); return viewGrocery();
      case 'copyprev': if (confirm('Replace this week with last week’s plan?')) { await planAction('/copy', { from: addDays(S.ws, -7) }); toast('Copied last week'); } return;
      case 'gen': await planAction('/generate', { mode: d.mode }); return toast(S.plan.totals.groceries > S.plan.totals.budget ? 'Plan is over budget — try raising it or removing meals' : 'Plan ready');
      case 'clear': return planAction('/clear');
      case 'pick': return openPick(+d.day, d.slot);
      case 'choose': closeModal(); return planAction('/entry', { day: S.pickCtx.day, slot: S.pickCtx.slot, recipe_id: +d.id });
      case 'lock': { const en = S.plan.entries.find(x => x.day == d.day && x.slot === d.slot); return planAction('/entry', { day: +d.day, slot: d.slot, locked: !en.locked }); }
      case 'rm': return planAction('/entry', { day: +d.day, slot: d.slot, remove: true });
      case 'fb': await api('POST', `/api/recipes/${d.id}/feedback`, { score: +d.v }); return viewPlan();
      case 'view': return viewRecipeModal(+d.id);
      case 'xadd': { const n = $('#xname').value; if (!n.trim()) return; await api('POST', `/api/week/${S.ws}/extras`, { name: n, price: $('#xprice').value }); return viewGrocery(); }
      case 'xdel': await api('DELETE', `/api/week/${S.ws}/extras/${d.id}`); return viewGrocery();
      case 'rslot': S.rfilter.slot = d.v; return viewRecipes();
      case 'rnew': return openRecipeForm(null);
      case 'redit': return openRecipeForm(+d.id);
      case 'rdel': if (confirm('Delete this recipe?')) { await api('DELETE', '/api/recipes/' + d.id); S.recipes = await api('GET', '/api/recipes'); viewRecipes(); } return;
      case 'ingadd': { const row = document.createElement('div'); row.innerHTML = ingRow({}, ''); $('#rf-ings').appendChild(row.firstElementChild); return; }
      case 'ingdel': return el.closest('.ing').remove();
      case 'rsave': return saveRecipe();
      case 'pnew': return openPersonForm(null);
      case 'pedit': return openPersonForm(+d.id);
      case 'pdel': if (confirm('Remove this person?')) { await api('DELETE', '/api/people/' + d.id); S.people = (await api('GET', '/api/bootstrap')).people; viewHousehold(); } return;
      case 'psave': await api(S.editPerson ? 'PUT' : 'POST', '/api/people' + (S.editPerson ? '/' + S.editPerson : ''), readPerson('pf-'));
        S.people = (await api('GET', '/api/bootstrap')).people; closeModal(); return viewHousehold();
      case 'pbadd': return openPbForm();
      case 'pbsave': await api('POST', '/api/pricebook', { name: $('#nb-name').value, unit: $('#nb-unit').value, price: $('#nb-price').value, aisle: $('#nb-aisle').value, flags: $('#nb-flags').value });
        S.pb = await api('GET', '/api/pricebook'); closeModal(); return viewPrices();
      case 'pbdel': if (confirm('Remove ' + d.name + ' from the price book?')) { await api('DELETE', '/api/pricebook?name=' + encodeURIComponent(d.name)); S.pb = await api('GET', '/api/pricebook'); viewPrices(); } return;
      case 'pbimport': return openImport();
      case 'csvgo': { const r = await api('POST', '/api/pricebook/import', { csv: $('#csv').value }); S.pb = await api('GET', '/api/pricebook'); closeModal(); viewPrices(); return toast('Imported ' + r.imported + ' items'); }
      case 'stsave': S.boot.settings = await api('PUT', '/api/settings', { household_name: $('#st-name').value, store_name: $('#st-store').value, currency: $('#st-cur').value });
        return toast('Saved');
      case 'authgo': return doAuth(d.mode);
      case 'authmode': return showAuth(d.mode);
      case 'hdel': if (confirm('Permanently delete the family (' + d.name + ') and ALL of their plans and recipes? This cannot be undone.')) { await api('DELETE', '/api/site/households/' + d.id); viewSettings(); } return;
      case 'invnew': { const r = await api('POST', '/api/invites'); const link = location.origin + '/?invite=' + r.code;
        return openModal(`<div class="row spread"><h3>Invite created</h3><button class="ghost" data-a="close">✕</button></div>
          <p class="muted small">Send this to the person you’re inviting. It works once and expires in 7 days. It is shown only now, so copy it before closing.</p>
          <label>Invite link<input readonly value="${esc(link)}" style="width:100%"></label>
          <label style="margin-top:10px">Or just the code<input readonly value="${esc(r.code)}" style="width:100%"></label>
          <div class="row" style="margin-top:12px"><button class="primary" data-a="invdone">Done</button></div>`); }
      case 'invdone': closeModal(); return viewSettings();
      case 'invdel': await api('DELETE', '/api/invites/' + d.id); return viewSettings();
      case 'logout': await api('POST', '/api/auth/logout'); S.user = null; S.boot = null; return showAuth('login');
      case 'print': return window.print();
      case 'shopstart': { const items = shopItems().filter(i => !i.checked); if (!items.length) return toast('Nothing left to buy'); S.shop = { items, i: 0 }; return renderShop(); }
      case 'shopdone': { const it = S.shop.items[S.shop.i]; if (!it.extra) api('POST', `/api/week/${S.ws}/grocery/check`, { name: it.name, checked: true }).catch(() => {}); S.shop.i++; return renderShop(); }
      case 'shopskip': S.shop.i++; return renderShop();
      case 'shopback': S.shop.i = Math.max(0, S.shop.i - 1); return renderShop();
      case 'listcopy': return toast((await copyText(listText(true))) ? 'List copied' : 'Could not copy');
      case 'listshare': try { await navigator.share({ title: 'Grocery list', text: listText(true) }); } catch (e) { /* cancelled */ } return;
      case 'rimport': return openModal(`<div class="row spread"><h3>Import a recipe from a web page</h3><button class="ghost" data-a="close">✕</button></div>
        <p class="muted small">Paste the address of a single recipe page (most recipe sites publish machine-readable recipe data). You’ll review and edit everything before it’s saved. Use this for your own personal meal planning, and respect each site’s terms.</p>
        <input id="imp-url" placeholder="https://…" style="width:100%"><div class="row" style="margin-top:12px"><button class="primary" data-a="impgo">Fetch recipe</button><button data-a="close">Cancel</button></div>`);
      case 'impgo': { const url = $('#imp-url').value; toast('Fetching…'); const draft = await api('POST', '/api/recipes/import-url', { url }); return openRecipeForm(null, draft); }
      case 'pwsave': await api('POST', '/api/auth/password', { current: $('#pw-cur').value, new: $('#pw-new').value }); $('#pw-cur').value = ''; $('#pw-new').value = ''; return toast('Password changed');
      case 'uadd': await api('POST', '/api/users', { username: $('#nu-name').value, password: $('#nu-pass').value, role: $('#nu-admin').checked ? 'admin' : 'member' }); toast('Account added'); return viewSettings();
      case 'upw': { const p = prompt('New password for ' + d.name + ' (8+ characters):'); if (!p) return; await api('PUT', '/api/users/' + d.id, { password: p }); return toast('Password reset'); }
      case 'urole': await api('PUT', '/api/users/' + d.id, { role: d.role }); return viewSettings();
      case 'udel': if (confirm('Remove the account "' + d.name + '"?')) { await api('DELETE', '/api/users/' + d.id); viewSettings(); } return;
      case 'wznext': if (S.wiz.step === 1) wizSyncPeople(); if (S.wiz.step === 1 && !S.wiz.people.some(p => p.name.trim())) return toast('Add at least one person'); S.wiz.step++; return renderWizard();
      case 'wzback': if (S.wiz.step === 1) wizSyncPeople(); S.wiz.step--; return renderWizard();
      case 'wzpadd': wizSyncPeople(); S.wiz.people.push(blankPerson()); return renderWizard();
      case 'wzpdel': wizSyncPeople(); S.wiz.people.splice(+d.idx, 1); return renderWizard();
      case 'wzpreset': { const v = d.v; for (let i = 0; i < 7; i++) SLOTS.forEach(s => S.wiz.grid[i][s] = s === 'dinner' || (v !== 'd' && s === 'lunch') || ((v === 'all' || v === 'allsn') && s === 'breakfast') || (v === 'allsn' && s === 'snack')); return renderWizard(); }
      case 'wzfinish': return wizFinish();
    }
  });
});

document.addEventListener('change', e => {
  const el = e.target, c = el.dataset.c; if (!c) return;
  guard(async () => {
    if (c === 'grid') {
      const g = el.dataset.scope === 'wz' ? S.wiz.grid : S.plan.week.grid;
      g[el.dataset.day][el.dataset.slot] = el.checked;
      if (el.dataset.scope === 'wk') return saveWeek({ grid: g });
    } else if (c === 'budget') return saveWeek({ budget: +el.value || 0 });
    else if (c === 'guests') return saveWeek({ guests: +el.value || 0 });
    else if (c === 'quick') return saveWeek({ quick: el.checked });
    else if (c === 'away') {
      const ids = new Set(S.plan.week.away);
      el.checked ? ids.delete(+el.dataset.id) : ids.add(+el.dataset.id);
      return saveWeek({ away: [...ids] });
    } else if (c === 'gcheck') {
      await api('POST', `/api/week/${S.ws}/grocery/check`, { name: el.dataset.name, checked: el.checked });
      el.closest('.list-item').classList.toggle('done', el.checked);
    } else if (c === 'signupopen') {
      await api('PUT', '/api/site', { signup_open: el.checked });
      S.signupOpen = el.checked; toast(el.checked ? 'Anyone can now create a family' : 'New family signup turned off');
    } else if (c === 'theme') {
      document.documentElement.dataset.theme = el.value;
      try { localStorage.setItem('plateful-theme', el.value); } catch (e) { /* ignore */ }
    } else if (c === 'pbf') {
      const tr = el.closest('tr'), p = S.pb.find(x => x.name === tr.dataset.name), k = el.dataset.k;
      p[k] = k === 'on_hand' ? el.checked : k === 'price' ? +el.value || 0 : el.value;
      await api('POST', '/api/pricebook', p); toast('Saved ' + p.name);
    }
  });
});

document.addEventListener('input', e => {
  const el = e.target, i = el.dataset.i; if (!i) return;
  if (i === 'wz') S.wiz[el.dataset.k] = el.dataset.k === 'budget' ? +el.value || 0 : el.value;
  else if (i === 'pickq') pickSearch(el.value);
  else if (i === 'rq') { S.rfilter.q = el.value; const pos = el.selectionStart; viewRecipes(); const n = $('[data-i=rq]'); n.focus(); n.setSelectionRange(pos, pos); }
  else if (i === 'pq') { S.pq = el.value; const pos = el.selectionStart; viewPrices(); const n = $('[data-i=pq]'); n.focus(); n.setSelectionRange(pos, pos); }
  else if (i === 'ingname') {
    const p = S.pb.find(x => x.name === el.value.trim().toLowerCase());
    if (p) el.closest('.ing').querySelectorAll('input')[2].value = p.unit;
  }
});

document.addEventListener('focusin', e => { if (e.target.matches && e.target.matches('input[readonly]')) e.target.select(); });
document.addEventListener('keydown', e => {
  if (e.key !== 'Enter') return;
  if (e.target.closest('.auth')) { const b = $('[data-a=authgo]'); if (b) b.click(); }
  else if (e.target.id === 'imp-url') $('[data-a=impgo]').click();
});
document.addEventListener('toggle', e => { if (e.target.id === 'wkset') S.wkOpen = e.target.open; }, true);

init();
