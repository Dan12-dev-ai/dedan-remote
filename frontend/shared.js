// ============================================================
// DEDAN Remote — shared API client, session store, UI helpers.
// Loaded by every page before page-specific scripts.
//
// Rules this file enforces:
//   * Every opportunity shown on screen comes from the API. There is
//     no client-side job generator anywhere in this app.
//   * The bearer token lives in localStorage; nothing secret is ever
//     put in a cookie or URL.
//   * Every request distinguishes "the server said no" (auth/validation
//     error we can show verbatim) from "the server never answered"
//     (network/500 — surfaced as a retryable failure).
// ============================================================

const API = (function () {
  const sameOrigin = window.location.origin;
  // When the page is served by the FastAPI app itself, /api is same-origin.
  // When served as loose files (file:// or a static dir) the API must be
  // named explicitly via `window.DEDAN_API_BASE`.
  //
  // This is resolved per request, not captured once at load: a config script
  // placed after this one would otherwise be ignored, and a devtools
  // override before reload has no effect either way.
  function base() {
    if (window.DEDAN_API_BASE) return String(window.DEDAN_API_BASE).replace(/\/+$/, '');
    return sameOrigin && sameOrigin.startsWith('http') ? sameOrigin + '/api/v1' : '/api/v1';
  }

  function token() { return localStorage.getItem('dedan_token') || null; }
  function setToken(t) { t ? localStorage.setItem('dedan_token', t) : localStorage.removeItem('dedan_token'); }
  function user() {
    try { return JSON.parse(localStorage.getItem('dedan_user') || 'null'); }
    catch (e) { return null; }
  }
  function setUser(u) { u ? localStorage.setItem('dedan_user', JSON.stringify(u)) : localStorage.removeItem('dedan_user'); }

  // ---- errors -------------------------------------------------
  class ApiError extends Error {
    constructor(status, code, message) {
      super(message || code || 'Request failed');
      this.name = 'ApiError';
      this.status = status;
      this.code = code;
    }
    get isAuth() { return this.status === 401 || this.status === 403; }
    get isNotFound() { return this.status === 404; }
  }

  async function request(path, options = {}) {
    const opts = Object.assign({}, options);
    opts.headers = Object.assign({ 'Accept': 'application/json' }, options.headers || {});
    const tok = token();
    if (tok) opts.headers['Authorization'] = 'Bearer ' + tok;
    if (opts.body && !opts.headers['Content-Type']) opts.headers['Content-Type'] = 'application/json';

    let res;
    try {
      res = await fetch(base() + path, opts);
    } catch (e) {
      // fetch only rejects on transport failure: offline, DNS, CORS, TLS.
      throw new ApiError(0, 'network_error',
        'Could not reach DEDAN Remote. Check your connection and try again.');
    }

    const rid = res.headers.get('X-Request-ID');
    let payload = null;
    const text = await res.text();
    if (text) {
      try { payload = JSON.parse(text); }
      catch (e) { payload = null; }
    }

    if (!res.ok) {
      const err = (payload && payload.error) || {};
      const message = err.message
        || (res.status >= 500
          ? 'DEDAN Remote had a problem handling that. Please try again.'
          : 'That request was rejected.');
      const e = new ApiError(res.status, err.code || ('http_' + res.status), message);
      e.requestId = rid;
      throw e;
    }
    return payload;
  }

  return {
    base, request, token, setToken, user, setUser, ApiError,
    // ---- auth -------------------------------------------------
    async register(email, password, displayName) {
      const out = await request('/auth/register', {
        method: 'POST',
        body: JSON.stringify({ email, password, display_name: displayName || null }),
      });
      setToken(out.token); setUser(out.user);
      return out.user;
    },
    async login(email, password) {
      const out = await request('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ email, password }),
      });
      setToken(out.token); setUser(out.user);
      return out.user;
    },
    async logout() {
      try { if (token()) await request('/auth/logout', { method: 'POST' }); }
      catch (e) { /* a dead session still means "logged out here" */ }
      setToken(null); setUser(null);
    },
    async me() {
      const out = await request('/auth/me');
      setUser(out.user);
      return out.user;
    },
    isSignedIn() { return !!token(); },

    // ---- opportunities ---------------------------------------
    jobs(params = {}) {
      const qs = new URLSearchParams();
      Object.entries(params).forEach(([k, v]) => {
        if (v === undefined || v === null || v === '') return;
        if (Array.isArray(v)) v.forEach(x => qs.append(k, x));
        else qs.set(k, v);
      });
      const s = qs.toString();
      return request('/jobs' + (s ? '?' + s : ''));
    },
    job(slugOrId) { return request('/jobs/' + encodeURIComponent(slugOrId)); },
    sources() { return request('/sources'); },
    categories() { return request('/categories'); },
    stats() { return request('/stats'); },

    // ---- user data -------------------------------------------
    saveJob(slugOrId) { return request('/jobs/' + encodeURIComponent(slugOrId) + '/save', { method: 'POST' }); },
    unsaveJob(slugOrId) { return request('/jobs/' + encodeURIComponent(slugOrId) + '/save', { method: 'DELETE' }); },
    saved() { return request('/saved'); },
    savedIds() {
      return request('/saved').then(rows => new Set(rows.map(r => r.job.id))).catch(() => new Set());
    },
    applications() { return request('/applications'); },
    createApplication(jobId, status, note) {
      return request('/applications', {
        method: 'POST',
        body: JSON.stringify({ job_id: jobId, status: status || 'saved', note: note || null }),
      });
    },
    patchApplication(id, patch) {
      return request('/applications/' + encodeURIComponent(id), { method: 'PATCH', body: JSON.stringify(patch) });
    },
    profile() { return request('/profile'); },
    patchProfile(patch) { return request('/profile', { method: 'PATCH', body: JSON.stringify(patch) }); },

    // ---- interviews ------------------------------------------
    interviewStatus() { return request('/interview/status'); },
    interviews() { return request('/interview/sessions'); },
    startInterview(slugOrId, opts = {}) {
      return request('/interview/mocks', {
        method: 'POST',
        body: JSON.stringify(Object.assign({ slug: slugOrId }, opts)),
      });
    },
    answerQuestion(interviewId, questionId, text, inputMode) {
      return request('/interview/mocks/' + encodeURIComponent(interviewId) + '/answers', {
        method: 'POST',
        body: JSON.stringify({
          question_id: questionId,
          response_text: text || '',
          input_mode: inputMode || 'text',
        }),
      });
    },
    finishInterview(interviewId) {
      return request('/interview/mocks/' + encodeURIComponent(interviewId) + '/finish', { method: 'POST' });
    },
    interview(interviewId) { return request('/interview/mocks/' + encodeURIComponent(interviewId)); },
    roleSkills(slug) { return request('/interview/skills?slug=' + encodeURIComponent(slug)); },
    reportPdfUrl(interviewId) { return base() + '/interview/mocks/' + encodeURIComponent(interviewId) + '/report.pdf'; },
  };
})();

// ------------------------------------------------------------
// Small shared UI helpers
// ------------------------------------------------------------

function esc(value) {
  return String(value === null || value === undefined ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// Render plain text but keep paragraph breaks. Descriptions come from third
// parties and may contain newlines; they are escaped, never injected as HTML.
function escMultiline(value) {
  return esc(value).replace(/\r\n/g, '\n').split(/\n{2,}/)
    .map(p => '<p>' + p.replace(/\n/g, '<br>') + '</p>').join('');
}

function toast(message, kind) {
  let host = document.getElementById('toast');
  if (!host) {
    host = document.createElement('div');
    host.id = 'toast';
    host.className = 'toast';
    host.setAttribute('role', 'status');
    host.setAttribute('aria-live', 'polite');
    document.body.appendChild(host);
  }
  host.textContent = message;
  host.className = 'toast show' + (kind ? ' toast-' + kind : '');
  clearTimeout(host._tid);
  host._tid = setTimeout(() => { host.className = 'toast'; }, 3200);
}

function debounce(fn, ms) {
  let t;
  return function (...args) { clearTimeout(t); t = setTimeout(() => fn.apply(this, args), ms); };
}

function timeAgo(iso) {
  if (!iso) return 'date not given';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return 'date not given';
  const mins = Math.max(0, Math.round((Date.now() - then) / 60000));
  if (mins < 1) return 'just now';
  if (mins < 60) return mins + 'm ago';
  const hours = Math.round(mins / 60);
  if (hours < 24) return hours + 'h ago';
  const days = Math.round(hours / 24);
  if (days < 31) return days + 'd ago';
  const months = Math.round(days / 30.44);
  if (months < 12) return months + 'mo ago';
  return Math.round(months / 12) + 'y ago';
}

// ------------------------------------------------------------
// Session chrome: renders the header account area and guards
// pages that must not work for anonymous visitors.
// ------------------------------------------------------------

const Session = {
  user: null,
  onChange: [],

  async load() {
    if (!API.isSignedIn()) { this.user = null; return null; }
    try { this.user = await API.me(); }
    catch (e) {
      if (e.isAuth) { API.setToken(null); API.setUser(null); this.user = null; }
    }
    return this.user;
  },

  // `where` is shown on the gate so the user knows why they were stopped.
  require(where) {
    if (this.user) return true;
    location.href = 'login.html?next=' + encodeURIComponent(location.pathname + location.search)
      + (where ? '&for=' + encodeURIComponent(where) : '');
    return false;
  },
};

// Renders account links into any element with [data-account-slot].
function renderAccountChrome() {
  const slots = document.querySelectorAll('[data-account-slot]');
  if (!slots.length) return;
  const u = Session.user;
  const name = u && (u.display_name || u.email) || 'Account';
  slots.forEach(slot => {
    if (u) {
      slot.innerHTML =
        '<a class="acct-link" href="saved.html">Saved</a>' +
        '<a class="acct-link" href="interviews.html">Interviews</a>' +
        '<button class="acct-link" type="button" data-logout>' +
        '<span aria-hidden="true">⎋</span> Sign out</button>';
      const out = slot.querySelector('[data-logout]');
      if (out) out.addEventListener('click', async () => {
        await API.logout();
        toast('Signed out');
        setTimeout(() => location.reload(), 400);
      });
    } else {
      slot.innerHTML =
        '<a class="acct-link" href="login.html">Sign in</a>' +
        '<a class="acct-btn" href="register.html">Create account</a>';
    }
  });
  const names = document.querySelectorAll('[data-account-name]');
  names.forEach(n => { n.textContent = u ? name : ''; });
}

// ------------------------------------------------------------
// Shared styles for the chrome above. Kept here so pages do not
// each need a stylesheet; the tokens match the DEDAN palette.
// ------------------------------------------------------------
(function injectChromeStyles() {
  const css = `
  .toast{position:fixed;left:50%;bottom:24px;transform:translate(-50%,12px);
    background:#261912;color:#fdf9f2;padding:10px 16px;border-radius:10px;
    font:500 13px/1.4 Manrope,system-ui,sans-serif;box-shadow:0 8px 24px rgba(36,23,17,.24);
    opacity:0;pointer-events:none;transition:opacity .18s ease,transform .18s ease;z-index:200;max-width:90vw}
  .toast.show{opacity:1;transform:translate(-50%,0)}
  .toast-error{background:#93000a;color:#fff}
  .acct-link,.acct-btn{font:500 13px/1 Manrope,system-ui,sans-serif;color:#4f4541;
    text-decoration:none;padding:8px 12px;border-radius:8px;border:1px solid transparent;
    background:none;cursor:pointer;display:inline-flex;align-items:center;gap:6px}
  .acct-link:hover,.acct-btn:hover{color:#1c1c18;background:#f1ede6}
  .acct-btn{background:#261912;color:#fdf9f2}
  .acct-btn:hover{background:#000;color:#fff}
  a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,
  textarea:focus-visible,[tabindex]:focus-visible{outline:2px solid #76584a;outline-offset:2px}
  .skel{background:linear-gradient(90deg,#f1ede6 25%,#e6e2db 37%,#f1ede6 63%);
    background-size:400% 100%;animation:skel 1.3s ease infinite;border-radius:12px}
  @keyframes skel{0%{background-position:100% 50%}100%{background-position:0 50%}}
  @media (prefers-reduced-motion: reduce){.skel{animation:none}}
  `;
  const style = document.createElement('style');
  style.textContent = css;
  document.head.appendChild(style);
})();

// `const` at file scope is not visible to other classic <script> files, so
// every module here is published explicitly. Without these lines app.js sees
// "API is not defined" and the explore page renders an empty feed.
window.API = API;
window.Session = Session;
