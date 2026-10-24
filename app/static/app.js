// Lector — single-page web client. Vanilla ES module, no build step, no external requests.
// API contract: docs/API.md

/* =========================================================================
   Utilities
   ========================================================================= */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

class Raw { constructor(s) { this.s = s; } toString() { return this.s; } }
const raw = (s) => new Raw(s);
const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ESC[c]);
function val(v) {
  if (v == null || v === false) return '';
  if (v instanceof Raw) return v.s;
  if (Array.isArray(v)) return v.map(val).join('');
  return esc(v);
}
/** Tagged template that HTML-escapes every interpolation unless it is Raw (or an array of Raw). */
function html(strings, ...vals) {
  let out = strings[0];
  for (let i = 0; i < vals.length; i++) out += val(vals[i]) + strings[i + 1];
  return raw(out);
}
const setHTML = (el, r) => { el.innerHTML = (r instanceof Raw || Array.isArray(r)) ? val(r) : String(r); };

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const pct = (x) => Math.round(clamp(Number(x) || 0, 0, 1) * 100);

function fmtDur(sec) {
  sec = Math.max(0, Math.floor(Number(sec) || 0));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}
function fmtHuman(sec) {
  sec = Math.max(0, Math.round(Number(sec) || 0));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60);
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m`;
  return `${sec}s`;
}
function fmtBytes(n) {
  n = Number(n) || 0;
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(n >= 100 || i === 0 ? 0 : 1)} ${u[i]}`;
}
function fmtWhen(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}
/** Drop leading "Series — " prefixes (possibly repeated) from a title/volume label. */
function stripSeries(str, series) {
  if (!str || !series) return str || '';
  const re = new RegExp('^' + series.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s*[—–:,-]\\s*', 'i');
  let out = String(str), prev;
  do { prev = out; out = out.replace(re, ''); } while (out && out !== prev);
  return out || String(str);
}
const volLabel = (b) => stripSeries(b.volume_label, b.series_title);
const authorsOf = (b) => (Array.isArray(b?.authors) ? b.authors.join(', ') : (b?.authors || ''));
function stripHtml(s) {
  if (!s) return '';
  if (!/[<&]/.test(s)) return s;
  const doc = new DOMParser().parseFromString(String(s).replace(/<br\s*\/?>/gi, '\n').replace(/<\/p>/gi, '\n\n'), 'text/html');
  return (doc.body.textContent || '').replace(/\n{3,}/g, '\n\n').trim();
}
function hashHue(s) {
  let h = 0;
  for (const c of String(s || '')) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return h % 360;
}
function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}
const store = {
  get(k, d) { try { const v = localStorage.getItem('lector.' + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem('lector.' + k, JSON.stringify(v)); } catch { /* ignore */ } },
};

/* =========================================================================
   Icons (inline SVG, 24x24, stroke-based)
   ========================================================================= */

const ICONS = {
  play: '<polygon class="f" points="7 4 20 12 7 20 7 4"/>',
  pause: '<rect class="f" x="6" y="4" width="4" height="16" rx="1"/><rect class="f" x="14" y="4" width="4" height="16" rx="1"/>',
  back: '<path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3"/><polyline points="4 3 4 8 9 8"/>',
  fwd: '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3"/><polyline points="20 3 20 8 15 8"/>',
  prev: '<polygon class="f" points="19 20 9 12 19 4 19 20"/><line x1="5" y1="19" x2="5" y2="5"/>',
  next: '<polygon class="f" points="5 4 15 12 5 20 5 4"/><line x1="19" y1="5" x2="19" y2="19"/>',
  list: '<line x1="9" y1="6" x2="20" y2="6"/><line x1="9" y1="12" x2="20" y2="12"/><line x1="9" y1="18" x2="20" y2="18"/><circle cx="4.5" cy="6" r="1"/><circle cx="4.5" cy="12" r="1"/><circle cx="4.5" cy="18" r="1"/>',
  bookmark: '<path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
  gauge: '<path d="M12 14l4-4"/><path d="M3.3 19a10 10 0 1 1 17.4 0"/>',
  text: '<path d="M4 6h16M4 10h16M4 14h10M4 18h7"/>',
  chevL: '<polyline points="15 18 9 12 15 6"/>',
  chevR: '<polyline points="9 18 15 12 9 6"/>',
  search: '<circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
  sliders: '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>',
  trash: '<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>',
  x: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
  check: '<polyline points="20 6 9 17 4 12"/>',
  checkC: '<circle cx="12" cy="12" r="9"/><polyline points="8 12.5 11 15.5 16 9.5"/>',
  refresh: '<path d="M20.5 12a8.5 8.5 0 1 1-2.5-6"/><polyline points="21 3 21 9 15 9"/>',
  download: '<path d="M12 3v12"/><polyline points="7 10 12 15 17 10"/><path d="M5 21h14"/>',
  plus: '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  vol: '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M19 5a10 10 0 0 1 0 14"/>',
  mute: '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="22" y1="9" x2="16" y2="15"/><line x1="16" y1="9" x2="22" y2="15"/>',
  alert: '<circle cx="12" cy="12" r="9"/><line x1="12" y1="8" x2="12" y2="12.5"/><line x1="12" y1="16" x2="12.01" y2="16"/>',
  clock: '<circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/>',
  books: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>',
  headphones: '<path d="M3 18v-6a9 9 0 0 1 18 0v6"/><path d="M21 19a2 2 0 0 1-2 2h-1v-6h3zM3 19a2 2 0 0 0 2 2h1v-6H3z"/>',
  loader: '<path d="M21 12a9 9 0 1 1-6.2-8.6"/>',
  circle: '<circle cx="12" cy="12" r="8"/>',
  minusC: '<circle cx="12" cy="12" r="9"/><line x1="8" y1="12" x2="16" y2="12"/>',
  xC: '<circle cx="12" cy="12" r="9"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/>',
  server: '<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><line x1="7" y1="7.5" x2="7.01" y2="7.5"/><line x1="7" y1="16.5" x2="7.01" y2="16.5"/>',
  cpu: '<rect x="5" y="5" width="14" height="14" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>',
  db: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  book: '<path d="M2 4h6a4 4 0 0 1 4 4v13a3 3 0 0 0-3-3H2z"/><path d="M22 4h-6a4 4 0 0 0-4 4v13a3 3 0 0 1 3-3h7z"/>',
  minus: '<line x1="5" y1="12" x2="19" y2="12"/>',
};
function icon(name, cls = '') {
  const body = (ICONS[name] || '').replace(/class="f"/g, 'fill="currentColor" stroke="none"');
  return raw(`<svg class="i ${cls}" viewBox="0 0 24 24" aria-hidden="true" focusable="false">${body}</svg>`);
}
const STATUS_ICON = { done: 'checkC', running: 'loader', pending: 'circle', error: 'xC', skipped: 'minusC' };

/* =========================================================================
   Toasts & dialogs
   ========================================================================= */

function toast(msg, kind = 'info', ms = 4200) {
  const box = $('#toasts');
  const t = document.createElement('div');
  t.className = `toast ${kind}`;
  t.setAttribute('role', kind === 'err' ? 'alert' : 'status');
  setHTML(t, html`<span class="msg">${msg}</span><button type="button" aria-label="Dismiss">${icon('x')}</button>`);
  const close = () => { t.classList.add('out'); setTimeout(() => t.remove(), 260); };
  t.querySelector('button').onclick = close;
  box.appendChild(t);
  while (box.children.length > 4) box.firstChild.remove();
  setTimeout(close, ms);
}

const modalEl = () => $('#modal');
/** Generic modal. Resolves with FormData on submit, or null on cancel. */
function dialog({ title, body = '', ok = 'OK', cancel = 'Cancel', danger = false }) {
  const m = modalEl();
  return new Promise((resolve) => {
    let done = false;
    const finish = (v) => { if (done) return; done = true; if (m.open) m.close(); resolve(v); };
    setHTML(m, html`<form novalidate>
      <h2>${title}</h2>
      ${body}
      <div class="actions">
        ${cancel ? html`<button type="button" class="btn ghost" data-cancel>${cancel}</button>` : ''}
        <button type="submit" class="btn ${danger ? 'danger' : 'primary'}">${ok}</button>
      </div></form>`);
    const form = m.querySelector('form');
    form.onsubmit = (e) => { e.preventDefault(); finish(new FormData(form)); };
    form.addEventListener('keydown', (e) => {   // Enter submits from a textarea too; Shift+Enter = newline
      if (e.key === 'Enter' && !e.shiftKey && e.target.tagName === 'TEXTAREA') { e.preventDefault(); form.requestSubmit(); }
    });
    const c = m.querySelector('[data-cancel]');
    if (c) c.onclick = () => finish(null);
    m.onclose = () => finish(null);
    m.showModal();
    const f = form.querySelector('input, textarea, select');
    if (f) { f.focus(); if (f.select) f.select(); }
  });
}
async function confirmDialog(message, { title = 'Are you sure?', ok = 'Delete', danger = true } = {}) {
  const r = await dialog({ title, body: html`<p class="muted">${message}</p>`, ok, danger });
  return !!r;
}
async function promptDialog(title, label, { value = '', type = 'text', textarea = false, ok = 'Save', hint = '' } = {}) {
  const field = textarea
    ? html`<textarea name="v" rows="3">${value}</textarea>`
    : html`<input name="v" type="${type}" value="${value}" autocomplete="off">`;
  const r = await dialog({ title, body: html`${hint ? html`<p class="muted small">${hint}</p>` : ''}<label class="field"><span>${label}</span>${field}</label>`, ok });
  return r ? String(r.get('v') ?? '') : null;
}

/* =========================================================================
   API helper
   ========================================================================= */

class ApiError extends Error {
  constructor(status, detail, data) { super(detail); this.status = status; this.detail = detail; this.data = data; }
}
function detailText(data, status) {
  const d = data && typeof data === 'object' ? data.detail : null;
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) return d.map((x) => (x && x.msg ? `${(x.loc || []).slice(-1)[0] || ''} ${x.msg}`.trim() : String(x))).join('; ');
  if (typeof data === 'string' && data.length < 200) return data || `Request failed (${status})`;
  return `Request failed (${status})`;
}
/**
 * Every request goes through here. JSON in/out, cookie auth, 401 → login, `detail` → toast.
 * opts: {method, body, quiet (no toast), no401 (don't redirect on 401)}
 */
async function api(path, { method = 'GET', body, quiet = false, no401 = false } = {}) {
  const init = { method, credentials: 'same-origin', headers: { Accept: 'application/json' } };
  if (body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
  let res;
  try { res = await fetch(path, init); } catch (e) {
    if (!quiet) toast('Network error — is the Lector server reachable?', 'err');
    throw new ApiError(0, 'Network error');
  }
  const text = await res.text();
  let data = null;
  if (text) { try { data = JSON.parse(text); } catch { data = text; } }
  if (!res.ok) {
    const detail = detailText(data, res.status);
    if (res.status === 401 && !no401) {
      const wasIn = !!state.user;
      state.user = null;
      if (wasIn) toast('Your session expired — please sign in again.', 'info');
      const { path: p } = parseHash();
      if (p !== '/login' && p !== '/setup') { state.returnTo = location.hash; go('/login', true); }
      throw new ApiError(401, detail, data);
    }
    if (!quiet) toast(detail, 'err');
    throw new ApiError(res.status, detail, data);
  }
  return data;
}

/* =========================================================================
   App state, theme
   ========================================================================= */

const state = {
  setup: null,       // GET /api/setup
  user: null,        // User
  voices: null,      // GET /api/voices
  returnTo: null,
  route: '',
};

function applyTheme(t) {
  t = t || 'dark';
  store.set('theme', t);
  try { localStorage.setItem('lector.theme', t); } catch { /* ignore */ }   // raw copy for the inline boot script
  if (t === 'auto') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', t);
  const bg = getComputedStyle(document.body).getPropertyValue('--bg').trim() || '#121419';
  const meta = $('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', bg);
}

async function getVoices() {
  if (!state.voices) state.voices = await api('/api/voices');
  return state.voices;
}
function voiceOptions(voices, selected, { allowDefault = false, defaultLabel = 'Server default' } = {}) {
  const groups = new Map();
  for (const v of voices) {
    const k = v.language || 'Other';
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(v);
  }
  return html`${allowDefault ? html`<option value="" ${!selected ? raw('selected') : ''}>${defaultLabel}</option>` : ''}
    ${[...groups.entries()].map(([lang, vs]) => html`<optgroup label="${lang}">${vs.map((v) => html`
      <option value="${v.id}" ${v.id === selected ? raw('selected') : ''}>${v.name}${v.gender ? ` (${v.gender})` : ''}</option>`)}</optgroup>`)}`;
}

/* =========================================================================
   Covers & status helpers
   ========================================================================= */

function coverHtml(b, { cls = '', extra = '' } = {}) {
  const hue = hashHue(b?.title);
  return html`<div class="cover ${cls}" style="--ph1:hsl(${hue} 52% 44%);--ph2:hsl(${(hue + 48) % 360} 58% 24%)">
    <div class="ph" aria-hidden="true">${b?.title || ''}<small>${authorsOf(b)}</small></div>
    ${b?.cover_url ? html`<img class="cov" src="${b.cover_url}" alt="" loading="lazy" decoding="async">` : ''}
    ${extra}</div>`;
}
// Missing covers (404) → hide the <img> so the generated placeholder shows.
document.addEventListener('error', (e) => {
  const t = e.target;
  if (t && t.tagName === 'IMG' && t.classList.contains('cov')) t.remove();
}, true);

function bookStatus(b) {
  const rs = b.renditions || [];
  if (!rs.length) return { cls: 'none', text: 'No audio' };
  const active = rs.find((r) => r.status === 'running') || rs.find((r) => r.status === 'queued');
  if (active) {
    if (active.status === 'queued' && !genProg(active)) return { cls: 'run', text: 'Queued' };
    return { cls: 'run', text: `Generating ${pct(genProg(active))}%` };
  }
  if (rs.some((r) => r.status === 'done')) return { cls: 'ok', text: 'Ready' };
  if (rs.some((r) => r.status === 'paused')) return { cls: 'warn', text: 'Paused' };
  if (rs.some((r) => r.status === 'error')) return { cls: 'err', text: 'Error' };
  return { cls: 'none', text: 'Cancelled' };
}
const REND_STATUS = {
  queued: ['run', 'Queued'], running: ['run', 'Generating'], paused: ['warn', 'Paused'],
  done: ['ok', 'Ready'], error: ['err', 'Error'], cancelled: ['none', 'Cancelled'],
};
function rendChip(r) {
  const [cls, label] = REND_STATUS[r.status] || ['none', r.status];
  const extra = r.status === 'running' || (r.status === 'paused' && genProg(r)) ? ` ${pct(genProg(r))}%` : '';
  return html`<span class="chip ${cls}">${label}${extra}</span>`;
}
const isActive = (r) => ['queued', 'running', 'paused'].includes(r.status);
/** Generation fraction 0..1: detailed responses use `generation_progress`, plain Rendition objects use `progress`. */
const genProg = (r) => (typeof r?.generation_progress === 'number' ? r.generation_progress : typeof r?.progress === 'number' ? r.progress : 0);
/** One line describing what the worker is doing on this rendition right now (running only). */
function liveLine(r) {
  const l = r && r.live;
  if (!l || r.status !== 'running') return '';
  const where = (r.total_chapters || 0) > 1 && l.chapter_title ? `${l.chapter_title} · ` : '';
  const chunk = l.chunks ? `segment ${l.chunk}/${l.chunks}` : 'starting…';
  const eta = l.eta_seconds != null ? ` · about ${fmtEta(l.eta_seconds)} left` : '';
  const rtf = l.realtime_factor ? ` · ${l.realtime_factor}× realtime` : '';
  return html`<div class="rend-sub live">${icon('headphones')} Narrating ${where}${chunk}${eta}${rtf}</div>`;
}
function fmtEta(sec) {
  if (sec < 60) return 'a minute';
  const m = Math.round(sec / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  return `${h} h ${m % 60} min`;
}
/** The signed-in user's Progress object attached to a detailed rendition (or null). */
const userProg = (r) => (r && r.progress && typeof r.progress === 'object' ? r.progress : null);

/* =========================================================================
   Router
   ========================================================================= */

function parseHash() {
  const h = location.hash.replace(/^#/, '') || '/';
  const [p, q] = h.split('?');
  return { path: p || '/', query: new URLSearchParams(q || '') };
}
function go(path, replace = false) {
  const target = '#' + path;
  if (replace) { history.replaceState(null, '', target); route(); }
  else if (location.hash === target) route();
  else location.hash = path;
}

const ROUTES = [
  [/^\/$/, 'library', viewLibrary],
  [/^\/book\/([^/]+)$/, 'book', viewBook],
  [/^\/player\/([^/]+)$/, 'player', viewPlayer],
  [/^\/settings$/, 'settings', viewSettings],
  [/^\/admin$/, 'admin', viewAdmin],
  [/^\/login$/, 'login', viewLogin],
  [/^\/setup$/, 'setup', viewSetup],
];

let routeToken = 0;
let cleanups = [];
function runCleanups() { for (const fn of cleanups.splice(0)) { try { fn(); } catch { /* ignore */ } } }

async function route() {
  runCleanups();
  const token = ++routeToken;
  const { path, query } = parseHash();

  if (state.setup?.needs_setup && path !== '/setup') return go('/setup', true);
  if (!state.setup?.needs_setup && path === '/setup') return go(state.user ? '/' : '/login', true);
  if (!state.user && path !== '/login' && path !== '/setup') {
    state.returnTo = location.hash;
    return go('/login', true);
  }
  if (state.user && path === '/login') return go('/', true);

  let match = null;
  for (const [re, name, fn] of ROUTES) {
    const m = path.match(re);
    if (m) { match = { name, fn, params: m.slice(1).map(decodeURIComponent) }; break; }
  }
  if (!match) return go('/', true);
  if (match.name === 'admin' && !state.user?.is_admin) { toast('Admins only.', 'err'); return go('/', true); }

  state.route = match.name;
  renderChrome();
  const root = $('#view');
  const ctx = {
    alive: () => token === routeToken,
    onCleanup: (fn) => cleanups.push(fn),
    every: (ms, fn) => { const id = setInterval(fn, ms); cleanups.push(() => clearInterval(id)); return id; },
  };
  window.scrollTo(0, 0);
  try {
    await match.fn(root, ctx, match.params, query);
  } catch (e) {
    if (!ctx.alive()) return;
    if (e instanceof ApiError && e.status === 401) return;
    console.error(e);
    setHTML(root, html`<div class="page"><div class="empty">${icon('alert')}<p>${e.detail || e.message || 'Something went wrong.'}</p>
      <a class="btn" href="#/">Back to library</a></div></div>`);
  }
}

function renderChrome() {
  const authed = !!state.user && !['login', 'setup'].includes(state.route);
  $('#topbar').hidden = !authed;
  if (authed) {
    const link = (href, name, ic, label) => html`<a href="${href}" class="${state.route === name ? 'active' : ''}" aria-label="${label}" ${state.route === name ? raw('aria-current="page"') : ''}>${icon(ic)}<span class="lbl">${label}</span></a>`;
    setHTML($('#nav'), html`
      ${link('#/', 'library', 'books', 'Library')}
      ${state.user.is_admin ? link('#/admin', 'admin', 'shield', 'Admin') : ''}
      ${link('#/settings', 'settings', 'sliders', 'Settings')}
      <button type="button" id="logout" aria-label="Sign out">${icon('logout')}<span class="lbl">Sign out</span></button>`);
    $('#logout').onclick = logout;
  }
  renderMini();
}

async function logout() {
  await playerUnload();
  try { await api('/api/auth/logout', { method: 'POST', quiet: true, no401: true }); } catch { /* ignore */ }
  state.user = null;
  state.returnTo = null;
  go('/login');
}

/* =========================================================================
   Player engine (one <audio> element for the whole session)
   ========================================================================= */

const audio = document.getElementById('audio');
const P = {
  rid: null,          // loaded rendition id (string)
  r: null,            // rendition + {book, chapters, progress, bookmarks}
  book: null,
  ch: 0,              // chapter index
  progress: null,
  bookmarks: [],
  timeline: [],
  tlCh: -1,
  para: -1,
  rate: 1,
  pendingSeek: null,  // seek to apply once metadata is loaded
  waiting: false,     // current chapter not generated yet
  autoResume: false,  // start playback when the awaited chapter becomes ready
  finished: false,
  sleep: { mode: null, left: 0 },   // mode: null | 'min' | 'eoc'
  pollId: null,
  lastSave: 0,
  loading: false,
  listeners: new Set(),
};
const emit = (type) => P.listeners.forEach((fn) => { try { fn(type); } catch (e) { console.error(e); } });
const chapters = () => P.r?.chapters || [];
const curChapter = () => chapters()[P.ch];
const curPos = () => (P.pendingSeek != null ? P.pendingSeek : audio.currentTime || 0);
function curDur() {
  if (isFinite(audio.duration) && audio.duration > 0 && audio.getAttribute('src')) return audio.duration;
  return curChapter()?.duration || 0;
}
const audioUrl = (i) => `/api/renditions/${encodeURIComponent(P.rid)}/chapters/${i}/audio`;
const skipBack = () => Number(state.user?.skip_back_seconds) || 15;
const skipFwd = () => Number(state.user?.skip_forward_seconds) || 30;
const clampRate = (r) => Math.round(clamp(Number(r) || 1, 0.5, 3) * 10) / 10;
const isPlayable = (c) => c && c.status === 'done';

function estDurations() {
  const chs = chapters();
  let d = 0, c = 0;
  for (const x of chs) if (isPlayable(x) && x.duration && x.char_count) { d += x.duration; c += x.char_count; }
  const perChar = c ? d / c : 0.065;   // ~15 chars/s fallback
  return chs.map((x) => (isPlayable(x) && x.duration ? x.duration : (x.char_count || 0) * perChar));
}
function bookPosition() {
  const est = estDurations();
  let before = 0;
  for (let i = 0; i < P.ch && i < est.length; i++) before += est[i];
  const total = est.reduce((a, b) => a + b, 0) || 1;
  const pos = before + Math.min(curPos(), curDur() || curPos());
  return { pos, total, frac: clamp(pos / total, 0, 1) };
}

async function playerOpen(rid, chParam) {
  rid = String(rid);
  if (P.rid === rid && P.r) {
    if (chParam != null && chParam !== P.ch) await setChapter(chParam, 0, true);
    return;
  }
  if (P.rid) await playerUnload();
  P.loading = true;
  const [r, prog] = await Promise.all([
    api(`/api/renditions/${encodeURIComponent(rid)}`),
    api(`/api/progress/${encodeURIComponent(rid)}`, { quiet: true }).catch(() => null),
  ]);
  P.loading = false;
  Object.assign(P, {
    rid, r, book: r.book || {}, bookmarks: sortBookmarks(r.bookmarks || []),
    progress: prog || userProg(r), timeline: [], tlCh: -1, para: -1,
    finished: false, waiting: false, autoResume: false, pendingSeek: null,
  });
  const pr = P.progress || {};
  P.rate = clampRate(pr.playback_rate || state.user?.playback_rate || 1);
  let ch = Number(pr.chapter_index) || 0;
  let pos = Number(pr.position) || 0;
  if (pr.finished) { ch = 0; pos = 0; }
  if (chParam != null) { pos = chParam === ch && !pr.finished ? pos : 0; ch = chParam; }
  ch = clamp(ch, 0, Math.max(0, chapters().length - 1));
  if (chParam == null && !isPlayable(chapters()[ch])) {
    const first = chapters().findIndex(isPlayable);
    if (first >= 0 && first > ch) { ch = first; pos = 0; }
  }
  emit('load');
  await setChapter(ch, pos, chParam != null);
  maybePoll();
}

async function playerUnload() {
  if (!P.rid) return;
  if (!audio.paused) audio.pause();
  await saveProgress();
  stopPoll();
  clearSleep();
  audio.removeAttribute('src');
  delete audio.dataset.src;
  audio.load();
  Object.assign(P, { rid: null, r: null, book: null, timeline: [], bookmarks: [], progress: null, waiting: false });
  if ('mediaSession' in navigator) navigator.mediaSession.metadata = null;
  emit('unload');
  renderMini();
}

async function setChapter(i, pos = 0, autoplay = false) {
  const chs = chapters();
  if (!chs.length || i < 0 || i >= chs.length) return;
  const changed = i !== P.ch;
  P.ch = i;
  P.finished = false;
  const c = chs[i];
  if (!isPlayable(c)) {
    if (!audio.paused) audio.pause();
    P.waiting = true;
    P.autoResume = autoplay || P.autoResume;
    P.pendingSeek = pos;
    audio.removeAttribute('src');
    delete audio.dataset.src;
    P.timeline = []; P.tlCh = -1; P.para = -1;
    emit('chapter');
    maybePoll(true);
    return;
  }
  P.waiting = false;
  P.autoResume = false;
  const url = audioUrl(i);
  if (audio.dataset.src !== url) {
    audio.dataset.src = url;
    P.pendingSeek = pos;
    audio.src = url;
    audio.defaultPlaybackRate = P.rate;
    audio.playbackRate = P.rate;
    audio.load();
  } else {
    audio.currentTime = pos;
  }
  loadTimeline(i);
  updateMediaMeta();
  emit('chapter');
  if (autoplay) play();
  if (changed || pos === 0) saveProgress();
}

function play() {
  if (P.waiting || !audio.getAttribute('src')) { if (P.waiting) toast('This chapter is still being generated.', 'info'); return; }
  if (P.finished) { if (curDur() - curPos() < 1) audio.currentTime = 0; P.finished = false; }
  const p = audio.play();
  if (p && p.catch) p.catch((e) => { if (e.name === 'NotAllowedError') emit('state'); else if (e.name !== 'AbortError') console.warn(e); });
}
const pause = () => audio.pause();
const togglePlay = () => (audio.paused ? play() : pause());

function seek(t) {
  const d = curDur();
  t = clamp(t, 0, d ? d - 0.25 : t);
  if (P.pendingSeek != null) P.pendingSeek = t; else audio.currentTime = t;
  emit('time');
}
const skip = (s) => seek(curPos() + s);

function prevChapter() {
  if (curPos() > 3 && !P.waiting) return seek(0);
  for (let j = P.ch - 1; j >= 0; j--) if (isPlayable(chapters()[j])) return setChapter(j, 0, !audio.paused);
  seek(0);
}
function nextChapter() {
  const chs = chapters();
  for (let j = P.ch + 1; j < chs.length; j++) {
    if (chs[j].status === 'skipped' || chs[j].status === 'error') continue;
    return setChapter(j, 0, !audio.paused || P.waiting);
  }
  toast('This is the last chapter.', 'info');
}

function setRate(r) {
  r = clampRate(r);
  P.rate = r;
  audio.defaultPlaybackRate = r;
  audio.playbackRate = r;
  emit('rate');
  persistRate(r);
}
const persistRate = debounce(async (r) => {
  saveProgress();
  try {
    const u = await api('/api/auth/me', { method: 'PUT', body: { playback_rate: r }, quiet: true });
    if (u) state.user = u;
  } catch { /* non-fatal */ }
}, 700);

function progressBody(extra = {}) {
  return { chapter_index: P.ch, position: Math.round(curPos() * 10) / 10, playback_rate: P.rate, finished: !!P.finished, ...extra };
}
async function saveProgress(extra) {
  if (!P.rid || !state.user || P.loading) return;
  P.lastSave = Date.now();
  try {
    const p = await api(`/api/progress/${encodeURIComponent(P.rid)}`, { method: 'PUT', body: progressBody(extra), quiet: true, no401: true });
    if (p) P.progress = p;
  } catch { /* offline: next save will retry */ }
}
const saveSoon = debounce(() => saveProgress(), 800);
function beacon() {
  if (!P.rid || !state.user) return;
  const url = `/api/progress/${encodeURIComponent(P.rid)}/beacon`;
  const json = JSON.stringify(progressBody());
  let ok = false;
  try { ok = navigator.sendBeacon(url, new Blob([json], { type: 'application/json' })); } catch { ok = false; }
  if (!ok) {
    try { fetch(url, { method: 'POST', body: json, headers: { 'Content-Type': 'application/json' }, keepalive: true, credentials: 'same-origin' }); } catch { /* ignore */ }
  }
}

async function onEnded() {
  if (P.sleep.mode === 'eoc') {
    clearSleep();
    await saveProgress();
    toast('Sleep timer: stopped at end of chapter.', 'info');
    return;
  }
  const chs = chapters();
  let j = P.ch + 1;
  while (j < chs.length && (chs[j].status === 'skipped' || chs[j].status === 'error')) j++;
  if (j >= chs.length) {
    P.finished = true;
    await saveProgress({ finished: true, position: Math.round(curDur() * 10) / 10 });
    emit('state');
    toast(`Finished “${P.book?.title || 'book'}”. Nice!`, 'ok', 6000);
    return;
  }
  await setChapter(j, 0, true);
  if (P.waiting) toast('Next chapter is still generating — it will start automatically when ready.', 'info', 6000);
}

/* --- polling for chapters that are still being generated --- */
function maybePoll(force = false) {
  const chs = chapters();
  const pending = chs.some((c) => c.status === 'pending' || c.status === 'running');
  if ((pending && (isActive(P.r) || force)) || P.waiting) {
    if (!P.pollId) P.pollId = setInterval(pollRendition, 15000);
  } else stopPoll();
}
function stopPoll() { if (P.pollId) { clearInterval(P.pollId); P.pollId = null; } }
async function pollRendition() {
  if (!P.rid) return stopPoll();
  const rid = P.rid;
  let r;
  try { r = await api(`/api/renditions/${encodeURIComponent(rid)}`, { quiet: true }); } catch { return; }
  if (P.rid !== rid) return;
  P.r = { ...P.r, ...r, book: r.book || P.book };
  emit('chapters');
  if (P.waiting && isPlayable(curChapter())) {
    const auto = P.autoResume;
    await setChapter(P.ch, P.pendingSeek || 0, auto);
    if (auto) toast('Next chapter is ready.', 'ok');
  }
  maybePoll();
}

/* --- timeline (read-along) --- */
async function loadTimeline(i) {
  if (P.tlCh === i && P.timeline.length) return;
  P.timeline = []; P.tlCh = i; P.para = -1;
  emit('timeline');
  const rid = P.rid;
  let tl = [];
  try { tl = await api(`/api/renditions/${encodeURIComponent(rid)}/chapters/${i}/timeline`, { quiet: true }); } catch { tl = []; }
  if (P.rid !== rid || P.ch !== i) return;
  P.timeline = Array.isArray(tl) ? tl : [];
  P.para = -1;
  emit('timeline');
  updatePara();
}
function updatePara() {
  const tl = P.timeline;
  if (!tl.length) return;
  const t = curPos();
  let idx = -1;
  for (let k = 0; k < tl.length; k++) { if (tl[k].start <= t + 0.05) idx = k; else break; }
  if (idx !== P.para) { P.para = idx; emit('para'); }
}

/* --- bookmarks --- */
const sortBookmarks = (b) => b.slice().sort((x, y) => x.chapter_index - y.chapter_index || x.position - y.position);
async function addBookmarkPrompt() {
  if (!P.rid || P.waiting) return;
  const ch = P.ch, pos = curPos();
  const note = await promptDialog('Add bookmark', 'Note (optional)', {
    textarea: true, ok: 'Add bookmark',
    hint: `${curChapter()?.title || `Chapter ${ch + 1}`} · ${fmtDur(pos)}`,
  });
  if (note === null) return;
  try {
    const bm = await api(`/api/renditions/${encodeURIComponent(P.rid)}/bookmarks`, {
      method: 'POST', body: { chapter_index: ch, position: Math.round(pos * 10) / 10, note: note.trim() || null },
    });
    P.bookmarks = sortBookmarks([...P.bookmarks, bm]);
    emit('bookmarks');
    toast('Bookmark added.', 'ok');
  } catch { /* toasted */ }
}
async function editBookmark(id) {
  const bm = P.bookmarks.find((b) => String(b.id) === String(id));
  if (!bm) return;
  const note = await promptDialog('Edit bookmark', 'Note', { value: bm.note || '', textarea: true });
  if (note === null) return;
  try {
    const nb = await api(`/api/bookmarks/${bm.id}`, { method: 'PUT', body: { note: note.trim() } });
    Object.assign(bm, nb || { note });
    emit('bookmarks');
  } catch { /* toasted */ }
}
async function deleteBookmark(id) {
  if (!(await confirmDialog('Delete this bookmark?', { title: 'Delete bookmark' }))) return;
  try {
    await api(`/api/bookmarks/${id}`, { method: 'DELETE' });
    P.bookmarks = P.bookmarks.filter((b) => String(b.id) !== String(id));
    emit('bookmarks');
  } catch { /* toasted */ }
}
function gotoBookmark(id) {
  const bm = P.bookmarks.find((b) => String(b.id) === String(id));
  if (!bm) return;
  if (bm.chapter_index !== P.ch) setChapter(bm.chapter_index, bm.position, true);
  else { seek(bm.position); play(); }
}

/* --- sleep timer --- */
let sleepTick = null, lastTick = 0;
function setSleep(mode) {
  clearSleep();
  if (!mode) { emit('sleep'); return; }
  if (mode === 'eoc') P.sleep = { mode: 'eoc', left: 0 };
  else P.sleep = { mode: 'min', left: Number(mode) * 60 };
  lastTick = performance.now();
  sleepTick = setInterval(onSleepTick, 250);
  emit('sleep');
}
function clearSleep() {
  if (sleepTick) clearInterval(sleepTick);
  sleepTick = null;
  P.sleep = { mode: null, left: 0 };
  audio.volume = 1;
  emit('sleep');
}
function sleepRemaining() {
  if (P.sleep.mode === 'min') return P.sleep.left;
  if (P.sleep.mode === 'eoc') return Math.max(0, (curDur() - curPos()) / (P.rate || 1));
  return 0;
}
function onSleepTick() {
  const now = performance.now();
  const dt = (now - lastTick) / 1000;
  lastTick = now;
  if (audio.paused) { audio.volume = 1; emit('sleep'); return; }
  if (P.sleep.mode === 'min') P.sleep.left -= dt;
  const rem = sleepRemaining();
  audio.volume = rem <= 10 ? clamp(rem / 10, 0, 1) : 1;      // fade out over the last 10 s
  if (P.sleep.mode === 'min' && rem <= 0) {
    audio.pause();
    clearSleep();
    toast('Sleep timer: playback paused.', 'info');
  }
  emit('sleep');
}
function sleepLabel() {
  if (!P.sleep.mode) return 'Sleep';
  const rem = sleepRemaining();
  if (P.sleep.mode === 'eoc') return 'End ch.';
  const m = Math.floor(rem / 60), s = Math.floor(rem % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}

/* --- MediaSession --- */
function updateMediaMeta() {
  if (!('mediaSession' in navigator) || !P.r) return;
  const b = P.book || {};
  const c = curChapter();
  const art = b.cover_url
    ? [{ src: new URL(b.cover_url, location.href).href, sizes: '512x512' }]
    : [{ src: new URL('/static/icons/icon.svg', location.href).href, sizes: '512x512', type: 'image/svg+xml' }];
  try {
    navigator.mediaSession.metadata = new MediaMetadata({
      title: c?.title || b.title || 'Lector', artist: authorsOf(b), album: b.title || '', artwork: art,
    });
  } catch { /* ignore */ }
}
let lastPosState = 0;
function updatePositionState() {
  if (!('mediaSession' in navigator) || !navigator.mediaSession.setPositionState) return;
  const now = Date.now();
  if (now - lastPosState < 1000) return;
  lastPosState = now;
  const d = curDur();
  if (!d || !isFinite(d)) return;
  try { navigator.mediaSession.setPositionState({ duration: d, playbackRate: P.rate, position: clamp(curPos(), 0, d) }); } catch { /* ignore */ }
}
function setupMediaSession() {
  if (!('mediaSession' in navigator)) return;
  const ms = navigator.mediaSession;
  const h = (a, fn) => { try { ms.setActionHandler(a, fn); } catch { /* unsupported */ } };
  h('play', () => play());
  h('pause', () => pause());
  h('stop', () => pause());
  h('seekbackward', (d) => skip(-(d?.seekOffset || skipBack())));
  h('seekforward', (d) => skip(d?.seekOffset || skipFwd()));
  h('previoustrack', () => prevChapter());
  h('nexttrack', () => nextChapter());
  h('seekto', (d) => { if (d && d.seekTime != null) seek(d.seekTime); });
}

/* --- audio element events --- */
audio.addEventListener('loadedmetadata', () => {
  audio.playbackRate = P.rate;
  if (P.pendingSeek != null) {
    const d = audio.duration;
    const t = P.pendingSeek;
    P.pendingSeek = null;
    if (t > 0) audio.currentTime = isFinite(d) && d > 0 ? Math.min(t, Math.max(0, d - 0.5)) : t;
  }
  emit('time');
});
audio.addEventListener('timeupdate', () => {
  emit('time');
  updatePara();
  updatePositionState();
  if (!audio.paused && Date.now() - P.lastSave > 10000) saveProgress();
});
audio.addEventListener('play', () => { if ('mediaSession' in navigator) navigator.mediaSession.playbackState = 'playing'; emit('state'); });
audio.addEventListener('pause', () => {
  if ('mediaSession' in navigator) navigator.mediaSession.playbackState = 'paused';
  emit('state');
  if (!audio.ended && P.rid) saveProgress();
});
audio.addEventListener('waiting', () => { P.buffering = true; emit('state'); });
audio.addEventListener('playing', () => { P.buffering = false; emit('state'); });
audio.addEventListener('canplay', () => { P.buffering = false; emit('state'); });
audio.addEventListener('seeked', () => { emit('time'); updatePara(); if (P.rid) saveSoon(); });
audio.addEventListener('ended', onEnded);
audio.addEventListener('volumechange', () => emit('volume'));
audio.addEventListener('error', () => {
  if (!audio.getAttribute('src') || !P.rid) return;
  P.buffering = false;
  emit('state');
  toast('Could not load this chapter’s audio.', 'err');
});

document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') beacon(); });
window.addEventListener('pagehide', beacon);

/* --- keyboard shortcuts --- */
document.addEventListener('keydown', (e) => {
  if (!P.rid || modalEl().open) return;
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const t = e.target;
  const isRange = t instanceof HTMLInputElement && t.type === 'range';
  if (!isRange && t.closest && t.closest('input, textarea, select, [contenteditable="true"]')) return;
  if (document.querySelector('.sheet') && e.key !== 'Escape') return;
  switch (e.key) {
    case ' ':
    case 'k':
      if (e.key === ' ' && t.closest && t.closest('button, a, summary')) return;
      e.preventDefault(); togglePlay(); break;
    case 'ArrowLeft':
      e.preventDefault(); if (e.shiftKey) prevChapter(); else skip(-skipBack()); break;
    case 'ArrowRight':
      e.preventDefault(); if (e.shiftKey) nextChapter(); else skip(skipFwd()); break;
    case '[': setRate(P.rate - 0.1); toast(`Speed ${P.rate.toFixed(1)}×`, 'info', 1200); break;
    case ']': setRate(P.rate + 0.1); toast(`Speed ${P.rate.toFixed(1)}×`, 'info', 1200); break;
    case 'b': case 'B': e.preventDefault(); addBookmarkPrompt(); break;
    case 'm': case 'M': audio.muted = !audio.muted; toast(audio.muted ? 'Muted' : 'Unmuted', 'info', 1200); break;
    default: break;
  }
});

/* =========================================================================
   Bottom sheets (chapters, speed, sleep)
   ========================================================================= */

let sheetClose = null;
function openSheet(title, bodyFn, onMount) {
  if (sheetClose) sheetClose();
  const back = document.createElement('div');
  back.className = 'sheet-backdrop';
  const sh = document.createElement('div');
  sh.className = 'sheet';
  sh.setAttribute('role', 'dialog');
  sh.setAttribute('aria-modal', 'true');
  sh.setAttribute('aria-label', title);
  setHTML(sh, html`<div class="grab" aria-hidden="true"></div>
    <div class="sheet-head"><h2>${title}</h2><button type="button" class="btn ghost icon" data-close aria-label="Close">${icon('x')}</button></div>
    <div class="sheet-body"></div>`);
  const body = sh.querySelector('.sheet-body');
  const renderBody = () => setHTML(body, bodyFn());
  renderBody();
  document.body.append(back, sh);
  const prevFocus = document.activeElement;
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  function close() {
    back.remove(); sh.remove();
    document.removeEventListener('keydown', onKey, true);
    sheetClose = null;
    if (cleanup) cleanup();
    if (prevFocus && prevFocus.focus) prevFocus.focus();
  }
  back.onclick = close;
  sh.querySelector('[data-close]').onclick = close;
  document.addEventListener('keydown', onKey, true);
  const cleanup = onMount ? onMount(sh, body, close, renderBody) : null;
  (sh.querySelector('.sel, .cur button, button:not([data-close])') || sh.querySelector('button')).focus({ preventScroll: true });
  sheetClose = close;
  return close;
}

function openChaptersSheet() {
  const body = () => html`<ul class="ch-list">${chapters().map((c, i) => {
    const ok = isPlayable(c);
    return html`<li class="ch-item ${ok ? '' : 'notready'} ${i === P.ch ? 'cur' : ''}">
      <span class="st ${c.status}" title="${c.status}">${icon(STATUS_ICON[c.status] || 'circle')}</span>
      <span class="ct" title="${c.title}"><span class="dim">${i + 1}.</span> ${c.title}</span>
      <span class="d">${ok ? fmtDur(c.duration) : (c.status === 'running' || c.status === 'pending' ? 'generating…' : c.status)}</span>
      <button type="button" class="btn sm icon ghost" data-ch="${i}" ${ok ? '' : raw('disabled')} aria-label="Play chapter ${i + 1}: ${c.title}">${icon(i === P.ch && !audio.paused ? 'pause' : 'play')}</button>
    </li>`;
  })}</ul>`;
  openSheet('Chapters', body, (sh, el, close, rerender) => {
    el.addEventListener('click', (e) => {
      const b = e.target.closest('[data-ch]');
      if (!b) return;
      const i = Number(b.dataset.ch);
      if (i === P.ch) togglePlay(); else setChapter(i, 0, true);
      close();
    });
    const cur = el.querySelector('.ch-item.cur');
    if (cur) cur.scrollIntoView({ block: 'center' });
    const fn = (t) => { if (t === 'chapters') rerender(); };
    P.listeners.add(fn);
    return () => P.listeners.delete(fn);
  });
}

const PRESETS = [0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 3];
function openSpeedSheet() {
  const body = () => html`
    <div class="speed-big" id="sp-val">${P.rate.toFixed(1)}×</div>
    <div class="speed-row">
      <button type="button" class="btn icon" data-d="-0.1" aria-label="Slower">${icon('minus')}</button>
      <input type="range" id="sp-range" min="0.5" max="3" step="0.1" value="${P.rate}" aria-label="Playback speed">
      <button type="button" class="btn icon" data-d="0.1" aria-label="Faster">${icon('plus')}</button>
    </div>
    <div class="presets">${PRESETS.map((p) => html`<button type="button" class="btn sm ${Math.abs(p - P.rate) < 0.01 ? 'primary' : ''}" data-p="${p}">${p}×</button>`)}</div>
    <p class="dim small" style="text-align:center;margin-top:10px">Saved for this book and as your default. Shortcut: [ and ]</p>`;
  openSheet('Playback speed', body, (sh, el) => {
    const sync = () => {
      $('#sp-val', el).textContent = `${P.rate.toFixed(1)}×`;
      const r = $('#sp-range', el);
      r.value = P.rate;
      r.style.setProperty('--pct', `${((P.rate - 0.5) / 2.5) * 100}%`);
      $$('[data-p]', el).forEach((b) => b.classList.toggle('primary', Math.abs(Number(b.dataset.p) - P.rate) < 0.01));
    };
    sync();
    el.addEventListener('input', (e) => { if (e.target.id === 'sp-range') { setRate(Number(e.target.value)); sync(); } });
    el.addEventListener('click', (e) => {
      const d = e.target.closest('[data-d]'); const p = e.target.closest('[data-p]');
      if (d) setRate(P.rate + Number(d.dataset.d));
      if (p) setRate(Number(p.dataset.p));
      if (d || p) sync();
    });
  });
}

const SLEEP_OPTS = [[null, 'Off'], [15, '15 minutes'], [30, '30 minutes'], [45, '45 minutes'], [60, '60 minutes'], ['eoc', 'End of chapter']];
function openSleepSheet() {
  const cur = P.sleep.mode === 'eoc' ? 'eoc' : null;
  const body = () => html`<ul class="opt-list">${SLEEP_OPTS.map(([v, l]) => {
    const sel = (v === null && !P.sleep.mode) || (v === 'eoc' && cur === 'eoc');
    return html`<li><button type="button" class="${sel ? 'sel' : ''}" data-v="${v ?? ''}"><span>${l}</span>${sel ? icon('check') : ''}</button></li>`;
  })}</ul>
  ${P.sleep.mode === 'min' ? html`<p class="muted small" style="padding:0 14px">Active: ${sleepLabel()} remaining. Fades out over the last 10 seconds.</p>` : ''}`;
  openSheet('Sleep timer', body, (sh, el, close) => {
    el.addEventListener('click', (e) => {
      const b = e.target.closest('[data-v]');
      if (!b) return;
      const v = b.dataset.v;
      setSleep(v === '' ? null : v === 'eoc' ? 'eoc' : Number(v));
      if (v) toast(v === 'eoc' ? 'Sleep at end of chapter.' : `Sleep in ${v} minutes.`, 'info', 2000);
      close();
    });
  });
}

/* =========================================================================
   Mini player
   ========================================================================= */

function renderMini() {
  const el = $('#mini');
  const show = !!P.rid && !!P.r && state.route !== 'player' && !!state.user && !['login', 'setup'].includes(state.route);
  el.hidden = !show;
  document.body.classList.toggle('has-mini', show);
  if (!show) { el.innerHTML = ''; return; }
  const c = curChapter();
  setHTML(el, html`<div class="bar"><i id="mini-bar" style="width:${bookPosition().frac * 100}%"></i></div>
    <div class="mini-in">
      <button type="button" class="mini-open" id="mini-open" aria-label="Open player: ${P.book?.title || ''}">
        ${coverHtml(P.book, { cls: 'square' })}
        <span class="txt"><div class="t1">${P.book?.title || ''}</div><div class="t2" id="mini-ch">${P.waiting ? 'Generating… ' : ''}${c?.title || ''}</div></span>
      </button>
      <button type="button" class="ctl" id="mini-back" aria-label="Skip back ${skipBack()} seconds">${icon('back')}</button>
      <button type="button" class="ctl" id="mini-play" aria-label="${audio.paused ? 'Play' : 'Pause'}">${icon(audio.paused ? 'play' : 'pause')}</button>
    </div>`);
  $('#mini-open').onclick = () => go(`/player/${P.rid}`);
  $('#mini-play').onclick = togglePlay;
  $('#mini-back').onclick = () => skip(-skipBack());
}
P.listeners.add((t) => {
  if (state.route === 'player') return;
  if (t === 'time') { const b = $('#mini-bar'); if (b) b.style.width = `${bookPosition().frac * 100}%`; return; }
  if (t === 'state') {
    const b = $('#mini-play');
    if (b) { setHTML(b, icon(audio.paused ? 'play' : 'pause')); b.setAttribute('aria-label', audio.paused ? 'Play' : 'Pause'); return; }
  }
  if (['state', 'chapter', 'load', 'unload'].includes(t)) renderMini();
});

/* =========================================================================
   Views: auth
   ========================================================================= */

function authShell(title, sub, formBody) {
  return html`<div class="auth"><div class="card">
    <div class="logo"><img src="/static/icons/icon.svg" alt=""><h1>${title}</h1><p class="muted small" style="margin:0">${sub}</p></div>
    ${formBody}</div></div>`;
}
const USER_RE = /^[A-Za-z0-9_.-]{2,64}$/;
function validateCreds(u, p) {
  if (!USER_RE.test(u)) return 'Username must be 2–64 characters: letters, digits, _ . -';
  if (p.length < 6) return 'Password must be at least 6 characters.';
  return null;
}
async function afterSignIn(res) {
  state.user = res.user;
  applyTheme(state.user.theme);
  const back = state.returnTo && !/login|setup/.test(state.returnTo) ? state.returnTo.replace(/^#/, '') : '/';
  state.returnTo = null;
  go(back, true);
}

async function viewLogin(root) {
  let mode = 'login';
  const allowReg = !!state.setup?.allow_registration;
  const render = () => {
    const reg = mode === 'register';
    setHTML(root, authShell(reg ? 'Create account' : 'Welcome back', reg ? 'Join this Lector server' : 'Sign in to Lector', html`
      <form id="f" novalidate>
        <label class="field"><span>Username</span><input name="username" type="text" autocomplete="username" autocapitalize="none" spellcheck="false" required></label>
        <label class="field"><span>Password</span><input name="password" type="password" autocomplete="${reg ? 'new-password' : 'current-password'}" required></label>
        <button class="btn primary" type="submit">${reg ? 'Create account' : 'Sign in'}</button>
      </form>
      ${allowReg ? html`<div class="alt">${reg ? 'Already have an account? ' : 'New here? '}<button type="button" class="linkbtn" id="sw">${reg ? 'Sign in' : 'Create an account'}</button></div>` : ''}`));
    const f = $('#f', root);
    f.username.focus();
    f.onsubmit = async (e) => {
      e.preventDefault();
      const u = f.username.value.trim(), p = f.password.value;
      if (reg) { const err = validateCreds(u, p); if (err) return toast(err, 'err'); }
      else if (!u || !p) return toast('Enter your username and password.', 'err');
      const btn = f.querySelector('button[type=submit]');
      btn.disabled = true;
      try {
        const res = await api(reg ? '/api/auth/register' : '/api/auth/login', { method: 'POST', body: { username: u, password: p }, no401: true });
        await afterSignIn(res);
      } catch { btn.disabled = false; f.password.select(); }
    };
    const sw = $('#sw', root);
    if (sw) sw.onclick = () => { mode = reg ? 'login' : 'register'; render(); };
  };
  render();
}

async function viewSetup(root) {
  setHTML(root, authShell('Set up Lector', 'Create the administrator account for this server.', html`
    <form id="f" novalidate>
      <label class="field"><span>Admin username</span><input name="username" type="text" autocomplete="username" autocapitalize="none" spellcheck="false" value="admin" required></label>
      <label class="field"><span>Password</span><input name="password" type="password" autocomplete="new-password" required minlength="6"></label>
      <label class="field"><span>Confirm password</span><input name="password2" type="password" autocomplete="new-password" required></label>
      <button class="btn primary" type="submit">Create admin account</button>
    </form>`));
  const f = $('#f', root);
  f.username.focus();
  f.onsubmit = async (e) => {
    e.preventDefault();
    const u = f.username.value.trim(), p = f.password.value;
    const err = validateCreds(u, p);
    if (err) return toast(err, 'err');
    if (p !== f.password2.value) return toast('Passwords do not match.', 'err');
    const btn = f.querySelector('button[type=submit]');
    btn.disabled = true;
    try {
      const res = await api('/api/setup', { method: 'POST', body: { username: u, password: p }, no401: true });
      state.setup = { ...state.setup, needs_setup: false };
      toast('Admin account created. Welcome to Lector!', 'ok');
      await afterSignIn(res);
    } catch { btn.disabled = false; }
  };
}

/* =========================================================================
   Views: library
   ========================================================================= */

const loadingHtml = html`<div class="loading-page"><span class="spinner" aria-label="Loading"></span></div>`;

async function viewLibrary(root, ctx) {
  setHTML(root, loadingHtml);
  let data = await api('/api/library');
  if (!ctx.alive()) return;
  let query = store.get('libQuery', '');
  let sort = store.get('libSort', 'title');

  setHTML(root, html`<div class="page">
    <section id="cont"></section>
    <section class="section" aria-labelledby="lib-h">
      <div class="section-head"><h2 id="lib-h">Library</h2><span class="muted small" id="lib-count"></span></div>
      <div class="lib-tools">
        <label class="search"><span class="sr-only">Search the library</span>${icon('search')}
          <input type="search" id="q" placeholder="Search title, author, series" value="${query}" autocomplete="off"></label>
        <label><span class="sr-only">Sort by</span>
          <select id="sort">
            <option value="title" ${sort === 'title' ? raw('selected') : ''}>Title</option>
            <option value="author" ${sort === 'author' ? raw('selected') : ''}>Author</option>
            <option value="recent" ${sort === 'recent' ? raw('selected') : ''}>Recently played</option>
          </select></label>
      </div>
      <div class="grid" id="grid" style="margin-top:18px"></div>
    </section></div>`);

  const renderContinue = () => {
    const items = data.continue || [];
    const el = $('#cont', root);
    if (!items.length) { el.innerHTML = ''; el.className = ''; return; }
    el.className = 'section';
    el.style.marginTop = '0';
    setHTML(el, html`<div class="section-head"><h2>Continue listening</h2></div>
      <div class="shelf" role="list">${items.map((it) => {
        const p = it.progress || {};
        const left = p.remaining_seconds != null ? `${fmtHuman(p.remaining_seconds)} left` : '';
        return html`<a role="listitem" class="shelf-item" href="#/player/${it.rendition.id}" aria-label="Continue ${it.book.title}">
          ${coverHtml(it.book, { extra: html`<span class="play-ov" aria-hidden="true">${icon('play')}</span>` })}
          <div class="t">${it.book.title}</div>
          <div class="dim small">${Math.round(p.percent || 0)}%${left ? ` · ${left}` : ''}</div>
          <div class="bar thin"><i style="width:${clamp(p.percent || 0, 0, 100)}%"></i></div></a>`;
      })}</div>`);
  };

  const card = (b, inSeries) => {
    const st = bookStatus(b);
    const p = b.progress;
    const vl = volLabel(b);
    const title = inSeries ? stripSeries(b.title, b.series_title) : b.title;
    const vol = vl && !(inSeries && title === vl) ? html`<span class="vol">${vl}</span>` : '';
    return html`<a class="book-card" href="#/book/${b.id}" ${inSeries ? raw('role="listitem"') : ''}>
      ${coverHtml(b, { extra: html`${vol}${b.missing ? html`<span class="missing">Missing</span>` : ''}` })}
      <div class="meta"><div class="t" title="${b.title}">${title}</div><div class="a">${authorsOf(b) || ' '}</div>
        <div class="chips"><span class="chip ${st.cls}">${st.text}</span></div>
        ${p && (p.percent > 0 || p.finished) ? html`<div class="bar thin" title="${Math.round(p.percent)}% listened"><i style="width:${p.finished ? 100 : clamp(p.percent, 0, 100)}%"></i></div>` : ''}
      </div></a>`;
  };

  const renderGrid = () => {
    const q = query.trim().toLowerCase();
    let books = data.books || [];
    if (q) books = books.filter((b) => [b.title, authorsOf(b), b.series_title].some((s) => s && String(s).toLowerCase().includes(q)));
    // group multi-volume series
    const groups = new Map();
    for (const b of books) {
      const key = b.series_title ? `s:${b.series_title}` : `b:${b.id}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(b);
    }
    const recentOf = (bs) => Math.max(0, ...bs.map((b) => Date.parse(b.progress?.updated_at || '') || 0));
    const list = [...groups.values()].map((bs) => ({
      books: bs,
      series: bs.length > 1 ? bs[0].series_title : null,
      title: (bs.length > 1 ? bs[0].series_title : bs[0].title) || '',
      author: authorsOf(bs[0]),
      recent: recentOf(bs),
    }));
    const cmp = (a, b) => a.localeCompare(b, undefined, { sensitivity: 'base', numeric: true });
    if (sort === 'author') list.sort((a, b) => cmp(a.author || '￿', b.author || '￿') || cmp(a.title, b.title));
    else if (sort === 'recent') list.sort((a, b) => b.recent - a.recent || cmp(a.title, b.title));
    else list.sort((a, b) => cmp(a.title.replace(/^(the|a|an)\s+/i, ''), b.title.replace(/^(the|a|an)\s+/i, '')));

    $('#lib-count', root).textContent = `${books.length} book${books.length === 1 ? '' : 's'}`;
    const grid = $('#grid', root);
    if (!books.length) {
      setHTML(grid, html`<div class="empty" style="grid-column:1/-1">${icon(q ? 'search' : 'books')}
        <p>${q ? `No books match “${query}”.` : 'Your library is empty.'}</p>
        ${!q && state.user?.is_admin ? html`<a class="btn" href="#/admin">Sync with Kavita</a>` : ''}</div>`);
      return;
    }
    setHTML(grid, list.map((g) => {
      if (!g.series) return card(g.books[0], false);
      const vols = g.books.slice().sort((a, b) => cmp(volLabel(a) || a.title, volLabel(b) || b.title));
      return html`<section class="series" aria-label="Series: ${g.series}">
        <div class="series-head">${icon('book')}<h3>${g.series}</h3><span class="chip none">${vols.length} volumes</span><span class="dim small grow" style="text-align:right">${g.author}</span></div>
        <div class="shelf" role="list">${vols.map((b) => card(b, true))}</div></section>`;
    }));
  };

  renderContinue();
  renderGrid();
  const q = $('#q', root);
  q.addEventListener('input', debounce(() => { query = q.value; store.set('libQuery', query); renderGrid(); }, 150));
  $('#sort', root).onchange = (e) => { sort = e.target.value; store.set('libSort', sort); renderGrid(); };

  // live refresh while something is generating
  ctx.every(15000, async () => {
    if (document.hidden) return;
    const generating = (data.books || []).some((b) => (b.renditions || []).some((r) => r.status === 'running' || r.status === 'queued'));
    if (!generating) return;
    try {
      const d = await api('/api/library', { quiet: true });
      if (!ctx.alive()) return;
      data = d;
      renderContinue();
      renderGrid();
    } catch { /* ignore */ }
  });
}

/* =========================================================================
   Views: book
   ========================================================================= */

let previewAudio = null;
function stopPreview() { if (previewAudio) { previewAudio.pause(); previewAudio = null; } }

async function viewBook(root, ctx, [bookId]) {
  setHTML(root, loadingHtml);
  const [book, voices] = await Promise.all([
    api(`/api/books/${encodeURIComponent(bookId)}`),
    getVoices().catch(() => ({ voices: [], default_voice: '', default_speed: 1 })),
  ]);
  if (!ctx.alive()) return;
  let b = book;
  const admin = !!state.user?.is_admin;
  const openSet = new Set();
  let fastPoll = false;
  ctx.onCleanup(stopPreview);

  const summary = stripHtml(b.summary);
  setHTML(root, html`<div class="page">
    <a class="back" href="#/">${icon('chevL')} Library</a>
    <div class="book-hero">
      ${coverHtml(b)}
      <div>
        ${b.series_title && (b.series_title !== b.title || b.volume_label) ? html`<div class="dim small" style="margin-bottom:4px">${b.series_title}${volLabel(b) ? ` · ${volLabel(b)}` : ''}</div>` : ''}
        <h1>${b.title}</h1>
        <div class="authors">${authorsOf(b)}</div>
        <div class="facts">
          ${b.is_gutenberg ? html`<span class="badge" title="Project Gutenberg header/footer and license were removed before narration">${icon('book')} Project Gutenberg text, boilerplate trimmed</span>` : ''}
          ${b.word_count ? html`<span class="chip none">${Number(b.word_count).toLocaleString()} words</span>` : ''}
          ${b.file_format ? html`<span class="chip none">${String(b.file_format).toUpperCase()}</span>` : ''}
          ${b.missing ? html`<span class="chip err">Missing from Kavita</span>` : ''}
        </div>
        ${summary ? html`<p class="summary clamp" id="sum">${summary}</p>
          ${summary.length > 320 ? html`<button type="button" class="linkbtn small" id="sum-more">Show more</button>` : ''}` : ''}
      </div>
    </div>
    <section class="section"><div class="section-head"><h2>Audiobooks</h2></div><div id="rends"></div></section>
    <section class="section" id="gen"></section>
  </div>`);

  const more = $('#sum-more', root);
  if (more) more.onclick = () => { const s = $('#sum', root); const c = s.classList.toggle('clamp'); more.textContent = c ? 'Show more' : 'Show less'; };

  const renderRends = () => {
    const rs = b.renditions || [];
    const el = $('#rends', root);
    if (!rs.length) {
      setHTML(el, html`<div class="card muted">${icon('headphones')} No audiobook yet. ${admin ? 'Generate one below.' : 'Request one below.'}</div>`);
      return;
    }
    setHTML(el, rs.map((r) => {
      const chs = r.chapters || [];
      const playable = chs.some(isPlayable) || r.done_chapters > 0;
      const pr = userProg(r);
      const resume = pr && !pr.finished && (pr.chapter_index > 0 || pr.position > 5);
      const acts = [];
      if (admin) {
        if (r.status === 'queued' || r.status === 'running') acts.push(html`<button type="button" class="btn sm" data-act="pause" data-rid="${r.id}">${icon('pause')} Pause</button>`);
        if (r.status === 'paused') acts.push(html`<button type="button" class="btn sm" data-act="resume" data-rid="${r.id}">${icon('play')} Resume</button>`);
        if (isActive(r)) acts.push(html`<button type="button" class="btn sm" data-act="cancel" data-rid="${r.id}">${icon('x')} Cancel</button>`);
        if (r.status === 'error' || r.status === 'cancelled') acts.push(html`<button type="button" class="btn sm" data-act="retry" data-rid="${r.id}">${icon('refresh')} Retry</button>`);
      }
      if (r.has_m4b) acts.push(html`<a class="btn sm" href="/api/renditions/${r.id}/m4b" download>${icon('download')} Download M4B</a>`);
      else if (r.status === 'done') acts.push(html`<button type="button" class="btn sm" data-act="m4b" data-rid="${r.id}">${icon('download')} Build M4B</button>`);
      if (admin) acts.push(html`<button type="button" class="btn sm danger" data-act="delete" data-rid="${r.id}">${icon('trash')} Delete</button>`);
      return html`<div class="card rend">
        <div class="rend-head">
          <div style="min-width:0">
            <div class="rend-title">${icon('headphones')} ${r.voice_name || r.voice} <span class="dim">· ${Number(r.speed || 1).toFixed(2)}×</span> ${rendChip(r)}</div>
            <div class="rend-sub">${r.done_chapters ?? 0}/${r.total_chapters ?? chs.length} chapters · ${fmtDur(r.total_duration)}${r.engine ? ` · ${r.engine}` : ''}${pr && pr.percent ? ` · ${Math.round(pr.percent)}% listened` : ''}</div>
            ${liveLine(r)}
          </div>
          ${playable ? html`<a class="btn primary" href="#/player/${r.id}" aria-label="${resume ? 'Resume' : 'Play'} ${r.voice_name || r.voice} audiobook">${icon('play')} ${resume ? 'Resume' : 'Play'}</a>` : ''}
        </div>
        ${isActive(r) ? html`<div class="bar ${r.status === 'running' ? 'run' : ''}" role="progressbar" aria-valuenow="${pct(genProg(r))}" aria-valuemin="0" aria-valuemax="100" aria-label="Generation progress"><i style="width:${pct(genProg(r))}%"></i></div>` : ''}
        ${r.error ? html`<div class="err-msg">${icon('alert')} ${r.error}</div>` : ''}
        ${acts.length ? html`<div class="rend-actions">${acts}</div>` : ''}
        ${chs.length ? html`<details data-rid="${r.id}" ${openSet.has(String(r.id)) ? raw('open') : ''}>
          <summary>${icon('chevR')} Chapters (${chs.length})</summary>
          <ul class="ch-list">${chs.map((c, i) => {
            const ok = isPlayable(c);
            return html`<li class="ch-item ${ok ? '' : 'notready'}">
              <span class="st ${c.status}" title="${c.status}">${icon(STATUS_ICON[c.status] || 'circle')}</span>
              <span class="ct" title="${c.title}"><span class="dim">${i + 1}.</span> ${c.title}</span>
              <span class="d">${ok ? fmtDur(c.duration) : c.status === 'running' ? 'generating…' : c.status === 'error' ? 'error' : c.status === 'skipped' ? 'skipped' : 'pending'}</span>
              ${ok ? html`<a class="btn sm icon ghost" href="#/player/${r.id}?ch=${i}" aria-label="Play chapter ${i + 1}: ${c.title}">${icon('play')}</a>` : html`<span style="width:32px"></span>`}
            </li>`;
          })}</ul></details>` : ''}
      </div>`;
    }));
    $$('details[data-rid]', el).forEach((d) => d.addEventListener('toggle', () => { if (d.open) openSet.add(d.dataset.rid); else openSet.delete(d.dataset.rid); }));
  };

  const refresh = async () => {
    try {
      const nb = await api(`/api/books/${encodeURIComponent(bookId)}`, { quiet: true });
      if (!ctx.alive()) return;
      b = nb;
      renderRends();
    } catch { /* ignore */ }
  };

  $('#rends', root).addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-act]');
    if (!btn) return;
    const { act, rid } = btn.dataset;
    if (act === 'delete') {
      if (!(await confirmDialog('This deletes the generated audio, chapters, everyone’s progress and bookmarks for this audiobook.', { title: 'Delete audiobook?' }))) return;
      try {
        await api(`/api/renditions/${rid}`, { method: 'DELETE' });
        if (P.rid === String(rid)) await playerUnload();
        toast('Audiobook deleted.', 'ok');
      } catch { return; }
      return refresh();
    }
    btn.disabled = true;
    try {
      if (act === 'm4b') {
        const res = await api(`/api/renditions/${rid}/m4b`, { method: 'POST' });
        if (res?.status === 'ready') toast('M4B is ready to download.', 'ok');
        else { toast('Building M4B in the background…', 'info'); fastPoll = true; }
      } else {
        await api(`/api/renditions/${rid}/${act}`, { method: 'POST' });
        toast({ pause: 'Paused.', resume: 'Resumed.', cancel: 'Cancelled.', retry: 'Re-queued.' }[act] || 'Done.', 'ok');
      }
    } catch { /* toasted */ }
    btn.disabled = false;
    refresh();
  });

  // Generate / request panel
  const vs = voices.voices || [];
  const defVoice = state.user?.preferred_voice || voices.default_voice || vs[0]?.id || '';
  const defSpeed = clamp(Number(voices.default_speed) || 1, 0.7, 1.5);
  setHTML($('#gen', root), html`<div class="card gen-panel">
    <h2>${admin ? 'Generate audiobook' : 'Request audiobook'}</h2>
    <p class="muted small">${admin ? 'Narrate this book with a local TTS voice. Chapters become playable as soon as each one is done.' : 'Ask the server to narrate this book. You can start listening as chapters finish.'}</p>
    ${vs.length ? html`<div class="grid2">
      <label class="field"><span>Voice</span><select id="g-voice">${voiceOptions(vs, defVoice)}</select></label>
      <label class="field"><span>Speed · <output class="speed-out" id="g-speed-out">${defSpeed.toFixed(2)}×</output></span>
        <input type="range" id="g-speed" min="0.7" max="1.5" step="0.05" value="${defSpeed}" aria-label="Narration speed"></label>
    </div>
    <div class="btn-group">
      <button type="button" class="btn" id="g-preview">${icon('play')} Preview voice</button>
      <button type="button" class="btn primary" id="g-go">${icon('headphones')} ${admin ? 'Generate audiobook' : 'Request audiobook'}</button>
    </div>` : html`<p class="muted">No voices available — is the TTS engine ready?</p>`}
  </div>`);
  if (vs.length) {
    const sp = $('#g-speed', root), out = $('#g-speed-out', root), sel = $('#g-voice', root), pv = $('#g-preview', root);
    const fill = () => { sp.style.setProperty('--pct', `${((sp.value - 0.7) / 0.8) * 100}%`); out.textContent = `${Number(sp.value).toFixed(2)}×`; if (previewAudio) previewAudio.playbackRate = Number(sp.value); };
    fill();
    sp.oninput = fill;
    const setPv = (on) => setHTML(pv, html`${icon(on ? 'pause' : 'play')} ${on ? 'Stop preview' : 'Preview voice'}`);
    sel.onchange = () => { stopPreview(); setPv(false); };
    pv.onclick = () => {
      if (previewAudio) { stopPreview(); setPv(false); return; }
      if (!audio.paused) audio.pause();
      previewAudio = new Audio(`/api/voices/${encodeURIComponent(sel.value)}/preview`);
      previewAudio.playbackRate = Number(sp.value);
      const a = previewAudio;
      a.onended = () => { if (previewAudio === a) { previewAudio = null; setPv(false); } };
      a.onerror = () => { if (previewAudio === a) { previewAudio = null; setPv(false); toast('Could not load the voice preview.', 'err'); } };
      setHTML(pv, html`<span class="spinner" style="width:16px;height:16px;border-width:2px"></span> Loading…`);
      a.onplaying = () => { if (previewAudio === a) setPv(true); };
      a.play().catch(() => { if (previewAudio === a) { previewAudio = null; setPv(false); } });
    };
    $('#g-go', root).onclick = async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      try {
        const r = await api(`/api/books/${encodeURIComponent(bookId)}/renditions`, { method: 'POST', body: { voice: sel.value, speed: Number(sp.value) }, quiet: true });
        const known = (b.renditions || []).some((x) => String(x.id) === String(r.id));
        toast(known ? 'That voice & speed already exists — it is queued.' : (admin ? 'Queued for narration.' : 'Request sent — narration is queued.'), 'ok');
        openSet.add(String(r.id));
        await refresh();
      } catch (err) {
        if (err.status === 403) toast(`${err.detail && !/^Request failed/.test(err.detail) ? err.detail + ' — ' : ''}Audiobook requests are turned off on this server. Ask an admin.`, 'err', 6000);
        else if (err.status !== 401) toast(err.detail, 'err');
      }
      btn.disabled = false;
    };
  }

  renderRends();
  let tick = 0;
  ctx.every(5000, () => {
    tick++;
    if (document.hidden) return;
    const rs = b.renditions || [];
    const running = rs.some((r) => r.status === 'running');
    const active = rs.some(isActive);
    if (fastPoll) { refresh(); if (rs.every((r) => r.has_m4b || r.status !== 'done')) fastPoll = false; return; }
    // A running narration updates its live segment counter every few seconds; queued/paused ones only need
    // an occasional check.
    if (running || (active && tick % 3 === 0)) refresh();
  });
}

/* =========================================================================
   Views: player
   ========================================================================= */

async function viewPlayer(root, ctx, [rid], query) {
  const chParam = query.has('ch') ? Number(query.get('ch')) : null;
  if (P.rid !== String(rid)) setHTML(root, loadingHtml);
  await playerOpen(rid, Number.isFinite(chParam) ? chParam : null);
  if (!ctx.alive()) return;
  if (chParam != null) history.replaceState(null, '', `#/player/${rid}`);
  renderMini();

  let tab = store.get('playerTab', 'text');
  let autoScroll = store.get('autoscroll', true);
  let userScrollAt = 0;
  let scrubbing = false;

  const b = P.book || {};
  setHTML(root, html`<div class="player">
    <div class="main">
      <a class="back" href="#/book/${b.id}">${icon('chevL')} ${b.title}</a>
      <div class="art">${coverHtml(b)}</div>
      <div class="np-title">
        <p class="bt">${b.title}</p>
        <div class="ct" id="p-ct"></div>
        <div class="au">${authorsOf(b)}</div>
      </div>
      <div class="scrub">
        <input type="range" id="p-scrub" min="0" max="100" step="0.1" value="0" aria-label="Seek within chapter">
        <div class="times"><span id="p-cur">0:00:00</span><span id="p-rem">-0:00:00</span></div>
      </div>
      <div class="controls">
        <button type="button" class="ctl" id="p-prev" aria-label="Previous chapter" title="Previous chapter (Shift+←)">${icon('prev')}</button>
        <button type="button" class="ctl skip" id="p-back" aria-label="Skip back ${skipBack()} seconds" title="Back ${skipBack()} s (←)">${icon('back')}<span class="secs">${skipBack()}</span></button>
        <button type="button" class="ctl big" id="p-play" aria-label="Play" title="Play/Pause (Space)">${icon('play')}</button>
        <button type="button" class="ctl skip" id="p-fwd" aria-label="Skip forward ${skipFwd()} seconds" title="Forward ${skipFwd()} s (→)">${icon('fwd')}<span class="secs">${skipFwd()}</span></button>
        <button type="button" class="ctl" id="p-next" aria-label="Next chapter" title="Next chapter (Shift+→)">${icon('next')}</button>
      </div>
      <div class="waiting" id="p-wait" hidden role="status">${icon('loader', 'st running')} This chapter is still generating — checking every 15 s…</div>
      <div class="book-prog">
        <div class="lbl"><span id="p-chn"></span><span id="p-bk"></span></div>
        <div class="bar thin"><i id="p-bkbar"></i></div>
      </div>
      <div class="tools">
        <button type="button" class="tool" id="t-speed" aria-label="Playback speed">${icon('gauge')}<span id="t-speed-l">1.0×</span></button>
        <button type="button" class="tool" id="t-sleep" aria-label="Sleep timer">${icon('moon')}<span id="t-sleep-l">Sleep</span></button>
        <button type="button" class="tool" id="t-bm" aria-label="Add bookmark (B)">${icon('bookmark')}<span>Bookmark</span></button>
        <button type="button" class="tool" id="t-ch" aria-label="Chapter list">${icon('list')}<span>Chapters</span></button>
        <button type="button" class="tool" id="t-mute" aria-label="Mute (M)">${icon('vol')}<span>Mute</span></button>
      </div>
    </div>
    <div class="side">
      <div class="tabs" role="tablist">
        <button type="button" role="tab" id="tab-text" aria-controls="pan-text">${icon('text')} Transcript</button>
        <button type="button" role="tab" id="tab-bm" aria-controls="pan-bm">${icon('bookmark')} Bookmarks <span class="dim" id="bm-n"></span></button>
      </div>
      <div class="panel" id="pan-text" role="tabpanel" aria-labelledby="tab-text">
        <div class="panel-tools"><span id="tl-info">Read along</span>
          <label class="check" style="margin:0"><input type="checkbox" id="tl-auto" ${autoScroll ? raw('checked') : ''}> Auto-scroll</label></div>
        <div class="transcript" id="tl" style="position:relative"></div>
      </div>
      <div class="panel" id="pan-bm" role="tabpanel" aria-labelledby="tab-bm">
        <div class="panel-tools"><span>Bookmarks</span><button type="button" class="btn sm" id="bm-add">${icon('plus')} Add at current time</button></div>
        <ul class="bm-list" id="bm-list"></ul>
      </div>
    </div>
  </div>`);

  const el = (id) => document.getElementById(id);
  const scrub = el('p-scrub');

  function setTab(t) {
    tab = t;
    store.set('playerTab', t);
    el('tab-text').setAttribute('aria-selected', String(t === 'text'));
    el('tab-bm').setAttribute('aria-selected', String(t === 'bm'));
    el('pan-text').hidden = t !== 'text';
    el('pan-bm').hidden = t !== 'bm';
    if (t === 'text') scrollToPara(true);
  }

  function updateTime() {
    const d = curDur(), t = curPos();
    if (!scrubbing) {
      scrub.max = d || 100;
      scrub.value = t;
      scrub.style.setProperty('--pct', `${d ? (t / d) * 100 : 0}%`);
      el('p-cur').textContent = fmtDur(t);
    }
    el('p-rem').textContent = `-${fmtDur(Math.max(0, d - t) / (P.rate || 1))}`;
    const bp = bookPosition();
    el('p-bkbar').style.width = `${bp.frac * 100}%`;
    el('p-bk').textContent = `${Math.round(bp.frac * 100)}% · ${fmtHuman((bp.total - bp.pos) / (P.rate || 1))} left`;
  }
  function updateState() {
    const btn = el('p-play');
    const playing = !audio.paused;
    setHTML(btn, icon(playing ? 'pause' : 'play'));
    btn.setAttribute('aria-label', playing ? 'Pause' : 'Play');
    btn.classList.toggle('loading', !!P.buffering && playing);
    btn.disabled = P.waiting;
  }
  function updateChapter() {
    const c = curChapter();
    const n = chapters().length;
    el('p-ct').textContent = c ? c.title : '';
    el('p-chn').textContent = n ? `Chapter ${P.ch + 1} of ${n}` : '';
    el('p-wait').hidden = !P.waiting;
    el('p-back').disabled = P.waiting;
    el('p-fwd').disabled = P.waiting;
    el('p-next').disabled = P.ch >= n - 1;
    scrub.disabled = P.waiting;
    updateState();
    updateTime();
  }
  function updateRate() { el('t-speed-l').textContent = `${P.rate.toFixed(1)}×`; el('t-speed').classList.toggle('on', Math.abs(P.rate - 1) > 0.01); updateTime(); }
  function updateSleep() { el('t-sleep-l').textContent = sleepLabel(); el('t-sleep').classList.toggle('on', !!P.sleep.mode); }
  function updateVolume() {
    const t = el('t-mute');
    setHTML(t, html`${icon(audio.muted ? 'mute' : 'vol')}<span>${audio.muted ? 'Unmute' : 'Mute'}</span>`);
    t.classList.toggle('on', audio.muted);
    t.setAttribute('aria-label', audio.muted ? 'Unmute (M)' : 'Mute (M)');
  }
  function renderTimeline() {
    const tl = el('tl');
    if (P.waiting) { setHTML(tl, html`<p class="muted" style="padding:12px">The transcript appears once this chapter has been narrated.</p>`); el('tl-info').textContent = 'Read along'; return; }
    if (!P.timeline.length) { setHTML(tl, html`<p class="muted" style="padding:12px">${P.tlCh === P.ch ? 'No transcript for this chapter.' : 'Loading transcript…'}</p>`); el('tl-info').textContent = 'Read along'; return; }
    el('tl-info').textContent = `${P.timeline.length} paragraphs`;
    setHTML(tl, P.timeline.map((p, i) => html`<p class="para ${p.heading ? 'h' : ''} ${i === P.para ? 'cur' : ''}" data-i="${i}" tabindex="0" role="button" aria-label="Seek to ${fmtDur(p.start)}">${p.text}</p>`));
    scrollToPara(true);
  }
  function scrollToPara(force) {
    const tl = el('tl');
    if (!tl || (!autoScroll && !force) || tab !== 'text') return;
    if (!force && Date.now() - userScrollAt < 4000) return;
    const cur = tl.querySelector('.para.cur');
    if (!cur) return;
    const top = cur.offsetTop - tl.clientHeight * 0.3;
    tl.scrollTo({ top: Math.max(0, top), behavior: force ? 'auto' : 'smooth' });
  }
  function updatePara() {
    const tl = el('tl');
    const prev = tl.querySelector('.para.cur');
    if (prev) prev.classList.remove('cur');
    const cur = tl.querySelector(`.para[data-i="${P.para}"]`);
    if (cur) cur.classList.add('cur');
    if (autoScroll) scrollToPara(false);
  }
  function renderBookmarks() {
    el('bm-n').textContent = P.bookmarks.length ? `(${P.bookmarks.length})` : '';
    const list = el('bm-list');
    if (!P.bookmarks.length) { setHTML(list, html`<li class="muted" style="padding:12px">No bookmarks yet. Press <b>B</b> or tap Bookmark to save your place.</li>`); return; }
    setHTML(list, P.bookmarks.map((bm) => {
      const c = chapters()[bm.chapter_index];
      return html`<li class="bm">
        <button type="button" class="go" data-go="${bm.id}" aria-label="Go to bookmark at ${fmtDur(bm.position)}">
          <div class="bt">${c ? c.title : `Chapter ${bm.chapter_index + 1}`} · <span class="mono">${fmtDur(bm.position)}</span></div>
          <div class="bn">${bm.note || fmtWhen(bm.created_at)}</div></button>
        <div class="btn-group">
          <button type="button" class="btn sm icon ghost" data-edit="${bm.id}" aria-label="Edit bookmark note">${icon('edit')}</button>
          <button type="button" class="btn sm icon ghost danger" data-del="${bm.id}" aria-label="Delete bookmark">${icon('trash')}</button>
        </div></li>`;
    }));
  }

  // wire controls
  el('p-play').onclick = togglePlay;
  el('p-back').onclick = () => skip(-skipBack());
  el('p-fwd').onclick = () => skip(skipFwd());
  el('p-prev').onclick = prevChapter;
  el('p-next').onclick = nextChapter;
  el('t-speed').onclick = openSpeedSheet;
  el('t-sleep').onclick = openSleepSheet;
  el('t-bm').onclick = addBookmarkPrompt;
  el('bm-add').onclick = addBookmarkPrompt;
  el('t-ch').onclick = openChaptersSheet;
  el('t-mute').onclick = () => { audio.muted = !audio.muted; };
  el('tab-text').onclick = () => setTab('text');
  el('tab-bm').onclick = () => setTab('bm');
  el('tl-auto').onchange = (e) => { autoScroll = e.target.checked; store.set('autoscroll', autoScroll); if (autoScroll) scrollToPara(true); };
  scrub.addEventListener('input', () => {
    scrubbing = true;
    const d = curDur();
    scrub.style.setProperty('--pct', `${d ? (scrub.value / d) * 100 : 0}%`);
    el('p-cur').textContent = fmtDur(scrub.value);
  });
  scrub.addEventListener('change', () => { scrubbing = false; seek(Number(scrub.value)); });
  const tlEl = el('tl');
  ['wheel', 'touchmove'].forEach((ev) => tlEl.addEventListener(ev, () => { userScrollAt = Date.now(); }, { passive: true }));
  tlEl.addEventListener('click', (e) => {
    const p = e.target.closest('.para');
    if (!p) return;
    const item = P.timeline[Number(p.dataset.i)];
    if (item) { seek(item.start); P.para = Number(p.dataset.i); updatePara(); if (audio.paused) play(); }
  });
  tlEl.addEventListener('keydown', (e) => { if ((e.key === 'Enter' || e.key === ' ') && e.target.classList.contains('para')) { e.preventDefault(); e.stopPropagation(); e.target.click(); } });
  el('bm-list').addEventListener('click', (e) => {
    const g = e.target.closest('[data-go]'), ed = e.target.closest('[data-edit]'), d = e.target.closest('[data-del]');
    if (g) gotoBookmark(g.dataset.go);
    else if (ed) editBookmark(ed.dataset.edit);
    else if (d) deleteBookmark(d.dataset.del);
  });

  const listener = (t) => {
    switch (t) {
      case 'time': updateTime(); break;
      case 'state': updateState(); break;
      case 'chapter': updateChapter(); renderTimeline(); break;
      case 'chapters': updateChapter(); renderBookmarks(); break;
      case 'timeline': renderTimeline(); break;
      case 'para': updatePara(); break;
      case 'rate': updateRate(); break;
      case 'sleep': updateSleep(); break;
      case 'volume': updateVolume(); break;
      case 'bookmarks': renderBookmarks(); break;
      case 'unload': go('/', true); break;
      default: break;
    }
  };
  P.listeners.add(listener);
  ctx.onCleanup(() => { P.listeners.delete(listener); if (sheetClose) sheetClose(); });

  setTab(tab);
  updateChapter(); updateRate(); updateSleep(); updateVolume(); renderTimeline(); renderBookmarks();
}

/* =========================================================================
   Views: settings
   ========================================================================= */

async function viewSettings(root, ctx) {
  setHTML(root, loadingHtml);
  const voices = await getVoices().catch(() => ({ voices: [] }));
  if (!ctx.alive()) return;
  const u = state.user;
  const SKIPS = [5, 10, 15, 20, 30, 45, 60, 90];
  const skipSel = (name, v) => html`<select name="${name}">${[...new Set([...SKIPS, Number(v)])].sort((a, b) => a - b).map((s) => html`<option value="${s}" ${s === Number(v) ? raw('selected') : ''}>${s} seconds</option>`)}</select>`;
  setHTML(root, html`<div class="page page-narrow">
    <h1>Settings</h1>
    <p class="muted">Signed in as <b>${u.username}</b>${u.is_admin ? ' · administrator' : ''}</p>
    <form class="card" id="prefs" novalidate>
      <h2>Playback</h2>
      <div class="gen-panel"><div class="grid2">
        <label class="field"><span>Skip back</span>${skipSel('skip_back_seconds', u.skip_back_seconds || 15)}</label>
        <label class="field"><span>Skip forward</span>${skipSel('skip_forward_seconds', u.skip_forward_seconds || 30)}</label>
      </div></div>
      <label class="field"><span>Default speed · <output class="speed-out" id="rate-out"></output></span>
        <input type="range" name="playback_rate" min="0.5" max="3" step="0.1" value="${u.playback_rate || 1}"></label>
      <label class="field"><span>Preferred narration voice</span>
        <select name="preferred_voice">${voiceOptions(voices.voices || [], u.preferred_voice || '', { allowDefault: true })}</select></label>
      <h2 style="margin-top:8px">Appearance</h2>
      <div class="btn-group" role="radiogroup" aria-label="Theme" id="theme">
        ${['dark', 'light', 'auto'].map((t) => html`<label class="check" style="margin-right:14px"><input type="radio" name="theme" value="${t}" ${(u.theme || 'dark') === t ? raw('checked') : ''}> ${t === 'auto' ? 'Match system' : t[0].toUpperCase() + t.slice(1)}</label>`)}
      </div>
      <div class="row" style="margin-top:10px"><button class="btn primary" type="submit">Save settings</button></div>
    </form>
    <form class="card" id="pw" novalidate>
      <h2>Change password</h2>
      <input type="text" name="username" value="${u.username}" autocomplete="username" hidden>
      <label class="field"><span>Current password</span><input type="password" name="current_password" autocomplete="current-password" required></label>
      <label class="field"><span>New password</span><input type="password" name="new_password" autocomplete="new-password" required minlength="6"></label>
      <label class="field"><span>Confirm new password</span><input type="password" name="new_password2" autocomplete="new-password" required></label>
      <button class="btn" type="submit">Update password</button>
    </form>
    <div class="card"><h2>Keyboard shortcuts</h2>
      <p class="muted small" style="margin:0">Space play/pause · ←/→ skip · Shift+←/→ chapter · [ ] speed · B bookmark · M mute</p></div>
  </div>`);
  const f = $('#prefs', root);
  const out = $('#rate-out', root);
  const rr = f.playback_rate;
  const fill = () => { out.textContent = `${Number(rr.value).toFixed(1)}×`; rr.style.setProperty('--pct', `${((rr.value - 0.5) / 2.5) * 100}%`); };
  fill();
  rr.oninput = fill;
  $('#theme', root).addEventListener('change', (e) => { if (e.target.name === 'theme') applyTheme(e.target.value); });
  f.onsubmit = async (e) => {
    e.preventDefault();
    const body = {
      skip_back_seconds: Number(f.skip_back_seconds.value),
      skip_forward_seconds: Number(f.skip_forward_seconds.value),
      playback_rate: Number(rr.value),
      preferred_voice: f.preferred_voice.value || null,
      theme: f.querySelector('input[name=theme]:checked')?.value || 'dark',
    };
    try {
      state.user = await api('/api/auth/me', { method: 'PUT', body });
      applyTheme(state.user.theme);
      if (P.rid && !P.progress?.playback_rate) setRate(body.playback_rate);
      toast('Settings saved.', 'ok');
    } catch { /* toasted */ }
  };
  const pw = $('#pw', root);
  pw.onsubmit = async (e) => {
    e.preventDefault();
    if (pw.new_password.value.length < 6) return toast('New password must be at least 6 characters.', 'err');
    if (pw.new_password.value !== pw.new_password2.value) return toast('New passwords do not match.', 'err');
    try {
      await api('/api/auth/password', { method: 'PUT', body: { current_password: pw.current_password.value, new_password: pw.new_password.value }, no401: true });
      pw.reset();
      toast('Password changed.', 'ok');
    } catch { /* toasted */ }
  };
}

/* =========================================================================
   Views: admin
   ========================================================================= */

async function viewAdmin(root, ctx) {
  setHTML(root, loadingHtml);
  const [status, users, settings, voices] = await Promise.all([
    api('/api/admin/status'),
    api('/api/admin/users'),
    api('/api/admin/settings'),
    getVoices().catch(() => ({ voices: [] })),
  ]);
  if (!ctx.alive()) return;
  let st = status;
  let us = users;

  setHTML(root, html`<div class="page">
    <h1>Admin</h1>
    <div class="stat-grid" id="a-status"></div>
    <section class="section"><div class="card" id="a-sync"></div></section>
    <section class="section"><div class="section-head"><h2>Narration queue</h2><span class="dim small">updates every 5 s</span></div><div class="card" id="a-queue"></div></section>
    <section class="section"><div class="section-head"><h2>Users</h2></div><div class="card" id="a-users"></div></section>
    <section class="section"><div class="section-head"><h2>Server settings</h2></div><form class="card" id="a-settings" novalidate></form></section>
  </div>`);

  const renderStatus = () => {
    const k = st.kavita || {}, en = st.engine || {}, w = st.worker || {}, sto = st.storage || {};
    const working = w && w.rendition_id != null;
    setHTML($('#a-status', root), html`
      <div class="card stat"><h3>${icon('server')} Kavita</h3>
        <div class="big row"><span class="dot ${!k.configured ? 'warn' : k.reachable ? 'ok' : 'err'}"></span>${!k.configured ? 'Not configured' : k.reachable ? 'Reachable' : 'Unreachable'}</div>
        <div class="muted small">${k.version ? `Version ${k.version}` : ''}</div>
        <div class="dim small" style="word-break:break-all">${k.url || ''}</div></div>
      <div class="card stat"><h3>${icon('cpu')} TTS engine</h3>
        <div class="big row"><span class="dot ${en.ready ? 'ok' : 'warn'}"></span>${en.name || 'Unknown'} ${en.ready ? '· ready' : '· not ready'}</div>
        <div class="muted small">${en.message || w.engine_message || ''}</div></div>
      <div class="card stat"><h3>${icon('headphones')} Worker</h3>
        ${working ? html`
          <div class="big" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${w.book_title || `Rendition ${w.rendition_id}`}</div>
          <div class="muted small" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">Chapter ${(w.chapter_index ?? 0) + 1}${w.chapter_title ? `: ${w.chapter_title}` : ''}</div>
          <div class="bar run"><i style="width:${w.chunks ? (w.chunk / w.chunks) * 100 : 0}%"></i></div>
          <div class="row between small"><span class="muted">Chunk ${w.chunk ?? 0} of ${w.chunks ?? '?'}</span>
            <span class="muted" title="Realtime factor">${w.realtime_factor ? `${Number(w.realtime_factor).toFixed(2)}× realtime` : ''}</span></div>`
          : html`<div class="big row"><span class="dot"></span>Idle</div><div class="muted small">Nothing being narrated.</div>`}</div>
      <div class="card stat"><h3>${icon('db')} Storage</h3>
        <div class="big">${fmtBytes(sto.audio_bytes)} <span class="muted small" style="font-weight:500">audio</span></div>
        <div class="muted small">${sto.free_bytes != null ? `${fmtBytes(sto.free_bytes)} free on disk` : ''}</div></div>`);
    const ls = st.last_sync;
    setHTML($('#a-sync', root), html`<div class="row between">
      <div class="grow"><h2 style="margin-bottom:4px">Kavita library sync</h2>
        <div class="muted small">${ls ? html`Last sync ${fmtWhen(ls.finished_at || ls.started_at)} · <span class="chip ${ls.status === 'ok' || ls.status === 'done' || ls.status === 'success' ? 'ok' : ls.status === 'running' ? 'run' : ls.status === 'error' ? 'err' : 'none'}">${ls.status || '—'}</span>
          ${ls.books_seen != null ? ` · ${ls.books_seen} books seen` : ''}${ls.message ? html`<div class="dim">${ls.message}</div>` : ''}` : 'Never synced.'}</div></div>
      <button type="button" class="btn primary" id="sync">${icon('refresh')} Sync Kavita now</button></div>`);
    $('#sync', root).onclick = async (e) => {
      e.currentTarget.disabled = true;
      try {
        const r = await api('/api/admin/sync', { method: 'POST' });
        toast(r?.status === 'busy' ? 'A sync is already running.' : 'Sync started.', 'info');
        setTimeout(poll, 1500);
      } catch { /* toasted */ }
    };
  };

  const qActions = (r) => html`<div class="btn-group">
    ${(r.status === 'queued' || r.status === 'running') ? html`<button type="button" class="btn sm icon" data-act="pause" data-rid="${r.id}" aria-label="Pause">${icon('pause')}</button>` : ''}
    ${r.status === 'paused' ? html`<button type="button" class="btn sm icon" data-act="resume" data-rid="${r.id}" aria-label="Resume">${icon('play')}</button>` : ''}
    ${isActive(r) ? html`<button type="button" class="btn sm icon" data-act="cancel" data-rid="${r.id}" aria-label="Cancel">${icon('x')}</button>` : ''}
    ${(r.status === 'error' || r.status === 'cancelled') ? html`<button type="button" class="btn sm icon" data-act="retry" data-rid="${r.id}" aria-label="Retry">${icon('refresh')}</button>` : ''}
    <button type="button" class="btn sm icon danger" data-act="delete" data-rid="${r.id}" aria-label="Delete">${icon('trash')}</button></div>`;
  const renderQueue = () => {
    const q = st.queue || [];
    const el = $('#a-queue', root);
    if (!q.length) { setHTML(el, html`<p class="muted" style="margin:0">The queue is empty.</p>`); return; }
    setHTML(el, html`<div class="table-wrap q-table"><table>
      <thead><tr><th>Book</th><th>Voice</th><th>Status</th><th style="width:28%">Progress</th><th></th></tr></thead>
      <tbody>${q.map((r) => html`<tr>
        <td><a href="#/book/${r.book_id}">${r.book_title || `Book ${r.book_id}`}</a>${r.error ? html`<div class="small" style="color:var(--err)">${r.error}</div>` : ''}</td>
        <td class="nowrap">${r.voice_name || r.voice} <span class="dim">${Number(r.speed || 1).toFixed(2)}×</span></td>
        <td>${rendChip(r)}</td>
        <td><div class="bar ${r.status === 'running' ? 'run' : ''}"><i style="width:${pct(genProg(r))}%"></i></div>
          <div class="dim small">${r.done_chapters ?? 0}/${r.total_chapters ?? '?'} chapters · ${pct(genProg(r))}%${r.live && r.live.eta_seconds != null ? ` · ~${fmtEta(r.live.eta_seconds)} left` : ''}</div></td>
        <td>${qActions(r)}</td></tr>`)}</tbody></table></div>
      <div class="q-cards">${q.map((r) => html`<div class="q-card">
        <div class="row between"><a href="#/book/${r.book_id}" class="grow" style="font-weight:600">${r.book_title || `Book ${r.book_id}`}</a>${rendChip(r)}</div>
        <div class="dim small">${r.voice_name || r.voice} · ${Number(r.speed || 1).toFixed(2)}× · ${r.done_chapters ?? 0}/${r.total_chapters ?? '?'} chapters</div>
        <div class="bar ${r.status === 'running' ? 'run' : ''}" style="margin:8px 0"><i style="width:${pct(genProg(r))}%"></i></div>
        ${qActions(r)}</div>`)}</div>`);
  };
  $('#a-queue', root).addEventListener('click', async (e) => {
    const b = e.target.closest('[data-act]');
    if (!b) return;
    const { act, rid } = b.dataset;
    try {
      if (act === 'delete') {
        if (!(await confirmDialog('Delete this audiobook and all its audio, progress and bookmarks?', { title: 'Delete audiobook?' }))) return;
        await api(`/api/renditions/${rid}`, { method: 'DELETE' });
        if (P.rid === String(rid)) await playerUnload();
        toast('Audiobook deleted.', 'ok');
      } else {
        await api(`/api/renditions/${rid}/${act}`, { method: 'POST' });
        toast({ pause: 'Paused.', resume: 'Resumed.', cancel: 'Cancelled.', retry: 'Re-queued.' }[act] || 'Done.', 'ok');
      }
    } catch { /* toasted */ }
    poll();
  });

  const renderUsers = () => {
    setHTML($('#a-users', root), html`<div class="table-wrap"><table class="users">
      <thead><tr><th>User</th><th>Admin</th><th>Created</th><th></th></tr></thead>
      <tbody>${us.map((u) => html`<tr>
        <td><span class="row" style="gap:8px">${icon('user')} <b>${u.username}</b>${u.id === state.user.id ? html`<span class="chip none">you</span>` : ''}</span></td>
        <td><label class="check" style="margin:0"><input type="checkbox" data-admin="${u.id}" ${u.is_admin ? raw('checked') : ''} aria-label="Administrator: ${u.username}"></label></td>
        <td class="dim small nowrap">${fmtWhen(u.created_at)}</td>
        <td><div class="btn-group" style="justify-content:flex-end">
          <button type="button" class="btn sm" data-pw="${u.id}" aria-label="Set password for ${u.username}">${icon('edit')}<span class="lbl">Password</span></button>
          <button type="button" class="btn sm icon danger" data-del="${u.id}" ${u.id === state.user.id ? raw('disabled') : ''} aria-label="Delete ${u.username}">${icon('trash')}</button></div></td>
      </tr>`)}</tbody></table></div>
      <form class="add-user" id="add-user" novalidate>
        <label class="field" style="margin:0"><span>New username</span><input name="username" type="text" autocomplete="off" autocapitalize="none" spellcheck="false"></label>
        <label class="field" style="margin:0"><span>Password</span><input name="password" type="password" autocomplete="new-password"></label>
        <label class="check"><input type="checkbox" name="is_admin"> Admin</label>
        <button class="btn primary" type="submit">${icon('plus')} Add user</button>
      </form>`);
    const f = $('#add-user', root);
    f.onsubmit = async (e) => {
      e.preventDefault();
      const u = f.username.value.trim(), p = f.password.value;
      const err = validateCreds(u, p);
      if (err) return toast(err, 'err');
      try {
        await api('/api/admin/users', { method: 'POST', body: { username: u, password: p, is_admin: f.is_admin.checked } });
        toast(`User ${u} created.`, 'ok');
        us = await api('/api/admin/users');
        renderUsers();
      } catch { /* toasted */ }
    };
  };
  $('#a-users', root).addEventListener('change', async (e) => {
    const cb = e.target.closest('[data-admin]');
    if (!cb) return;
    try {
      await api(`/api/admin/users/${cb.dataset.admin}`, { method: 'PUT', body: { is_admin: cb.checked } });
      toast('Role updated.', 'ok');
    } catch { cb.checked = !cb.checked; }
    try { us = await api('/api/admin/users', { quiet: true }); renderUsers(); } catch { /* ignore */ }
  });
  $('#a-users', root).addEventListener('click', async (e) => {
    const pwb = e.target.closest('[data-pw]'), del = e.target.closest('[data-del]');
    if (pwb) {
      const u = us.find((x) => String(x.id) === pwb.dataset.pw);
      const p = await promptDialog(`Set password for ${u?.username}`, 'New password', { type: 'password', ok: 'Set password' });
      if (p == null) return;
      if (p.length < 6) return toast('Password must be at least 6 characters.', 'err');
      try { await api(`/api/admin/users/${pwb.dataset.pw}`, { method: 'PUT', body: { password: p } }); toast('Password updated.', 'ok'); } catch { /* toasted */ }
    } else if (del) {
      const u = us.find((x) => String(x.id) === del.dataset.del);
      if (!(await confirmDialog(`Delete user “${u?.username}” and their progress and bookmarks?`, { title: 'Delete user?' }))) return;
      try {
        await api(`/api/admin/users/${del.dataset.del}`, { method: 'DELETE' });
        toast('User deleted.', 'ok');
        us = await api('/api/admin/users');
        renderUsers();
      } catch { /* toasted */ }
    }
  });

  const renderSettings = (s) => {
    const f = $('#a-settings', root);
    const sp = clamp(Number(s.default_speed) || 1, 0.7, 1.5);
    setHTML(f, html`<div class="gen-panel"><div class="grid2">
      <label class="field"><span>Default voice</span><select name="default_voice">${voiceOptions(voices.voices || [], s.default_voice)}</select></label>
      <label class="field"><span>Default narration speed · <output class="speed-out" id="ds-out">${sp.toFixed(2)}×</output></span>
        <input type="range" name="default_speed" min="0.7" max="1.5" step="0.05" value="${sp}"></label></div></div>
      <label class="check"><input type="checkbox" name="allow_user_requests" ${s.allow_user_requests ? raw('checked') : ''}> Allow regular users to request audiobooks</label>
      <label class="check"><input type="checkbox" name="allow_registration" ${s.allow_registration ? raw('checked') : ''}> Allow new users to register on the sign-in page</label>
      <button class="btn primary" type="submit">Save server settings</button>`);
    const r = f.default_speed;
    const fill = () => { $('#ds-out', f).textContent = `${Number(r.value).toFixed(2)}×`; r.style.setProperty('--pct', `${((r.value - 0.7) / 0.8) * 100}%`); };
    fill();
    r.oninput = fill;
    f.onsubmit = async (e) => {
      e.preventDefault();
      try {
        const ns = await api('/api/admin/settings', { method: 'PUT', body: {
          default_voice: f.default_voice.value, default_speed: Number(r.value),
          allow_user_requests: f.allow_user_requests.checked, allow_registration: f.allow_registration.checked,
        } });
        state.voices = null;   // default voice/speed changed
        if (state.setup) state.setup.allow_registration = !!(ns || {}).allow_registration;
        toast('Server settings saved.', 'ok');
        renderSettings(ns || s);
      } catch { /* toasted */ }
    };
  };

  async function poll() {
    if (!ctx.alive() || document.hidden) return;
    try { st = await api('/api/admin/status', { quiet: true }); } catch { return; }
    if (!ctx.alive()) return;
    renderStatus();
    renderQueue();
  }

  renderStatus();
  renderQueue();
  renderUsers();
  renderSettings(settings || st.settings || {});
  ctx.every(5000, poll);
}

/* =========================================================================
   Boot
   ========================================================================= */

async function boot() {
  applyTheme(store.get('theme', 'dark'));
  setupMediaSession();
  const root = $('#view');
  try {
    state.setup = await api('/api/setup', { quiet: true, no401: true });
  } catch (e) {
    setHTML(root, html`<div class="auth"><div class="card" style="text-align:center">${icon('alert')}
      <h2 style="margin-top:10px">Can’t reach the Lector server</h2><p class="muted">${e.detail || ''}</p>
      <button class="btn primary" type="button" id="retry">Try again</button></div></div>`);
    $('#retry').onclick = () => location.reload();
    return;
  }
  if (!state.setup.needs_setup) {
    try {
      state.user = await api('/api/auth/me', { quiet: true, no401: true });
      applyTheme(state.user.theme);
    } catch { state.user = null; }
  }
  window.addEventListener('hashchange', route);
  route();
  if ('serviceWorker' in navigator && (location.protocol === 'https:' || location.hostname === 'localhost' || location.hostname === '127.0.0.1')) {
    navigator.serviceWorker.register('/sw.js').catch(() => { /* optional */ });
  }
}

boot();
