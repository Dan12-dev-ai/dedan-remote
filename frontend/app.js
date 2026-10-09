// ============================================================
// DEDAN Remote — Explore Page Controller
// Every card on this page comes from GET /api/v1/jobs. There is no
// client-side job generator, so the feed can never show a role the
// backend does not actually have.
// ============================================================

const APP_STATE = {
  jobs: [],
  total: 0,
  page: 1,
  pageSize: 20,
  selectedJobId: null,
  savedIds: new Set(),
  viewMode: 'list',
  searchQuery: '',
  sortMode: 'match',
  isLoading: true,
  // A failed fetch must be distinguishable from "no matching jobs".
  // `error` is null while the feed is healthy.
  error: null,
  sources: [],
  categories: []
};

// Debounce timers keyed by name so a fast typist issues one request.
const _timers = {};

function debounce(name, fn, ms) {
  clearTimeout(_timers[name]);
  _timers[name] = setTimeout(fn, ms);
}

// ------------------------------
// Boot
// ------------------------------
document.addEventListener('DOMContentLoaded', () => {
  const path = window.location.pathname;

  buildMarquee();

  // Landing-page theme toggle (dark mode available site-wide via theme.js).
  const lt = document.getElementById('landing-theme');
  const ltIcon = document.getElementById('landing-theme-icon');
  const syncLandingTheme = () => {
    if (ltIcon && window.DEDAN_THEME) ltIcon.textContent = window.DEDAN_THEME.isDark() ? 'light_mode' : 'dark_mode';
  };
  if (lt) {
    syncLandingTheme();
    document.addEventListener('themechange', syncLandingTheme);
    lt.addEventListener('click', () => window.DEDAN_THEME && window.DEDAN_THEME.toggle());
    document.documentElement.classList.add('theme-ready');
  }

  // Smooth anchor scroll
  document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', (e) => {
      const id = anchor.getAttribute('href').substring(1);
      if (!id) return;
      const el = document.getElementById(id);
      if (el) { e.preventDefault(); el.scrollIntoView({ behavior:'smooth', block:'start' }); }
    });
  });

  if (path.includes('opportunities.html') || path.endsWith('/') || path.endsWith('/frontend/')) {
    if (document.getElementById('job-feed')) initExplorePage();
  }
  if (path.includes('job-detail.html')) initJobDetailPage();
});

// ------------------------------
// Landing marquee
// ------------------------------
// Real brand SVG marks for the "Trusted Remote Ecosystem" strip. Each entry is
// a self-contained inline SVG so the ticker never depends on an external image
// host (and never shows a broken-image box if one is unreachable).
const MARQUEE_LOGOS = [
  // Real remote-work / data platforms DEDAN monitors. Each is a self-contained
  // inline SVG wordmark so the ticker never depends on an external image host.
  { name: 'Appen', svg: '<svg viewBox="0 0 120 32" width="112" height="30" aria-hidden="true"><text x="0" y="24" font-family="Manrope,Inter,sans-serif" font-size="26" font-weight="800" letter-spacing="-0.5" fill="#0A2E36">Appen</text><circle cx="108" cy="20" r="5" fill="#E87722"/></svg>' },
  { name: 'TELUS International', svg: '<svg viewBox="0 0 40 32" width="34" height="30" aria-hidden="true"><rect x="0" y="2" width="38" height="28" rx="5" fill="#4B286D"/><text x="20" y="23" text-anchor="middle" font-family="Manrope,Inter,sans-serif" font-size="20" font-weight="800" fill="#fff">T</text></svg>' },
  { name: 'Toloka', svg: '<svg viewBox="0 0 120 32" width="108" height="30" aria-hidden="true"><text x="0" y="24" font-family="Manrope,Inter,sans-serif" font-size="24" font-weight="700" fill="#2B2B2B">Toloka</text><path d="M112 18 l6 -8 l6 8 z" fill="#00A9E0"/></svg>' },
  { name: 'Fiverr', svg: '<svg viewBox="0 0 110 32" width="100" height="30" aria-hidden="true"><text x="0" y="25" font-family="Manrope,Inter,sans-serif" font-size="27" font-weight="800" fill="#1DBF73">Fiverr</text><text x="92" y="25" font-family="Manrope,Inter,sans-serif" font-size="20" font-weight="800" fill="#1DBF73">.</text></svg>' },
  { name: 'DataAnnotation', svg: '<svg viewBox="0 0 40 32" width="34" height="30" aria-hidden="true"><rect x="0" y="2" width="38" height="28" rx="6" fill="#4F46E5"/><text x="20" y="23" text-anchor="middle" font-family="Manrope,Inter,sans-serif" font-size="18" font-weight="800" fill="#fff">DA</text></svg>' },
  { name: 'Upwork', svg: '<svg viewBox="0 0 120 32" width="112" height="30" aria-hidden="true"><text x="0" y="24" font-family="Manrope,Inter,sans-serif" font-size="25" font-weight="800" fill="#14A800">Upwork</text><circle cx="116" cy="21" r="4.5" fill="#14A800"/></svg>' },
  { name: 'Toptal', svg: '<svg viewBox="0 0 100 32" width="92" height="30" aria-hidden="true"><text x="0" y="24" font-family="Manrope,Inter,sans-serif" font-size="24" font-weight="800" letter-spacing="1" fill="#386EE7">TOPTAL</text></svg>' },
  { name: 'Scale AI', svg: '<svg viewBox="0 0 120 32" width="112" height="30" aria-hidden="true"><path d="M4 26 L14 8 L24 26 Z" fill="#3B00FF"/><text x="30" y="24" font-family="Manrope,Inter,sans-serif" font-size="23" font-weight="800" fill="#0B0B0B">Scale</text><text x="92" y="24" font-family="Manrope,Inter,sans-serif" font-size="23" font-weight="800" fill="#3B00FF">AI</text></svg>' },
  { name: 'Outlier', svg: '<svg viewBox="0 0 110 32" width="102" height="30" aria-hidden="true"><text x="0" y="24" font-family="Manrope,Inter,sans-serif" font-size="24" font-weight="800" fill="#1A1A1A">Outlier</text><circle cx="106" cy="12" r="4" fill="#5B5BD6"/></svg>' },
  { name: 'Turing', svg: '<svg viewBox="0 0 100 32" width="92" height="30" aria-hidden="true"><text x="0" y="24" font-family="Manrope,Inter,sans-serif" font-size="25" font-weight="800" letter-spacing="-0.5" fill="#0A2540">Turing</text></svg>' },
];

// Build the marquee: two identical halves so the -50% keyframe loop is seamless.
function buildMarquee() {
  const track = document.getElementById('marquee-track');
  if (!track) return;
  const makeHalf = () => {
    const half = document.createElement('div');
    half.className = 'flex items-center gap-12 md:gap-16 pr-12 md:pr-16 shrink-0';
    MARQUEE_LOGOS.forEach(logo => {
      const item = document.createElement('div');
      // Uniform height h-7 md:h-9; greyscale -> colour on hover; 70% -> 100%.
      item.className = 'shrink-0 h-7 md:h-9 flex items-center grayscale opacity-70 hover:grayscale-0 hover:opacity-100 transition-all duration-300';
      item.title = logo.name;
      item.innerHTML = logo.svg;
      const svg = item.firstElementChild;
      if (svg) { svg.setAttribute('height', '100%'); svg.removeAttribute('width'); svg.style.height = '100%'; svg.style.width = 'auto'; }
      half.appendChild(item);
    });
    return half;
  };
  track.appendChild(makeHalf());
  track.appendChild(makeHalf()); // duplicate => translateX(-50%) lands exactly on the copy
}

// ------------------------------
// Init Explore
// ------------------------------
async function initExplorePage() {
  const loading = document.getElementById('loading');
  loading?.classList.remove('hidden');
  APP_STATE.isLoading = true;
  APP_STATE.error = null;

  // Saved ids load independently: a 401 here must not blank the feed, it
  // just means the save buttons render in their "not saved" state.
  API.savedIds().then(ids => { APP_STATE.savedIds = ids; renderJobFeed(); });

  try {
    const data = await API.jobs({ page: 1, page_size: APP_STATE.pageSize });
    APP_STATE.jobs = data.items || [];
    APP_STATE.total = data.total || 0;
    APP_STATE.page = data.page || 1;
  } catch (err) {
    APP_STATE.jobs = [];
    APP_STATE.error = err;
  } finally {
    loading?.classList.add('hidden');
    APP_STATE.isLoading = false;
  }

  renderJobFeed();
  if (window.matchMedia('(min-width: 1536px)').matches && APP_STATE.jobs.length > 0) {
    selectJobForPreview(APP_STATE.jobs[0].slug, { silent: true });
  }

  bindExploreInteractions();
  bindFilterDrawer();
  await Promise.all([loadSources(), loadCategories()]);
}

// Facets are rendered from the live registry so the sidebar can never
// advertise a source the backend is not actually monitoring.
async function loadSources() {
  try {
    const rows = await API.sources();
    APP_STATE.sources = Array.isArray(rows) ? rows : (rows.items || []);
    renderSourceFilter();
  } catch (err) {
    // A missing facet is a smaller failure than a broken feed: keep the
    // feed and drop the facet, rather than erroring the whole page.
    console.warn('sources unavailable', err);
  }
}

async function loadCategories() {
  try {
    const rows = await API.categories();
    APP_STATE.categories = Array.isArray(rows) ? rows : (rows.items || []);
    renderCategoryFilter();
  } catch (err) {
    console.warn('categories unavailable', err);
  }
}

// Re-query the backend. Filters and search always re-run server-side:
// filtering a single loaded page client-side would silently contradict
// the result count.
async function refreshJobs() {
  APP_STATE.isLoading = true;
  APP_STATE.error = null;
  renderJobFeed();
  try {
    const params = { page: APP_STATE.page, page_size: APP_STATE.pageSize };
    if (APP_STATE.searchQuery) params.q = APP_STATE.searchQuery;
    if (APP_STATE.sortMode) params.sort = APP_STATE.sortMode;
    const data = await API.jobs(params);
    APP_STATE.jobs = data.items || [];
    APP_STATE.total = data.total || 0;
    APP_STATE.page = data.page || 1;
  } catch (err) {
    APP_STATE.jobs = [];
    APP_STATE.error = err;
  } finally {
    APP_STATE.isLoading = false;
    renderJobFeed();
  }
}

// ------------------------------
// Filter drawer
// ------------------------------
function bindFilterDrawer() {
  const drawer = document.getElementById('filter-drawer');
  const toggle = () => drawer?.classList.toggle('hidden');
  document.getElementById('toggle-filter-drawer')?.addEventListener('click', toggle);
  document.getElementById('close-filter-drawer')?.addEventListener('click', toggle);
  // Applying commits the selected facets and closes the drawer.
  document.getElementById('apply-filters-btn')?.addEventListener('click', () => {
    APP_STATE.page = 1;
    refreshJobs();
    toggle();
  });
}

// ------------------------------
// Bindings
// ------------------------------
function bindExploreInteractions() {
  const searchInput = document.getElementById('explore-q');
  if (searchInput) {
    searchInput.addEventListener('input', onSearchChange);
    searchInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); APP_STATE.page = 1; refreshJobs(); }
    });
  }
  // There is no "AI query parser". The box submits a real keyword search.
  document.getElementById('ai-run')?.addEventListener('click', () => {
    APP_STATE.page = 1; refreshJobs();
  });

  // View modes
  const vlist = document.getElementById('view-list');
  const vcompact = document.getElementById('view-compact');
  vlist?.addEventListener('click', () => setViewMode('list'));
  vcompact?.addEventListener('click', () => setViewMode('compact'));

  // Sort — maps the label to the API's sort token and re-queries.
  document.getElementById('sort')?.addEventListener('change', (e) => onSortChange(e.target.value));

  // Clear all filters
  document.getElementById('clear-all')?.addEventListener('click', clearAllFilters);
  document.getElementById('clear-empty')?.addEventListener('click', clearAllFilters);

  document.querySelectorAll('#parsed [data-filter]').forEach(chip => {
    chip.addEventListener('click', () => {
      const anim = chip.animate([{opacity:1, transform:'scale(1)'},{opacity:0, transform:'scale(.93)'}],{ duration:160, fill:'forwards'});
      anim.onfinish = () => { chip.remove(); showToast('Filter removed', 'filter_alt_off'); };
    });
  });

  // Save search
  document.getElementById('save-search')?.addEventListener('click', () => {
    showToast('Saved search · Python backend jobs · Africa · Remote', 'bookmark_added');
  });
  document.getElementById('save-search-b')?.addEventListener('click', () => {
    showToast('Saved search · Africa · Remote', 'bookmark_added');
  });

  // Drawer open triggers
  document.getElementById('open-drawer')?.addEventListener('click', openDrawer);
  document.getElementById('mobile-filters')?.addEventListener('click', openDrawer);

  // Voice
  document.getElementById('voice-btn')?.addEventListener('click', () => {
    showToast('Voice search coming soon', 'mic');
  });

  // ⌘K focuses search
  document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      const q = document.getElementById('explore-q') || document.getElementById('nav-q');
      if (q) { q.focus(); q.select(); }
    }
  });
}

function bindFilterDrawer() {
  document.getElementById('drawer-close')?.addEventListener('click', closeDrawer);
  document.getElementById('backdrop')?.addEventListener('click', closeDrawer);
  document.getElementById('drawer-apply')?.addEventListener('click', () => {
    closeDrawer();
    renderJobFeed();
    showToast('Filters applied', 'check_circle');
  });
  document.getElementById('drawer-clear')?.addEventListener('click', () => {
    clearAllFilters();
    closeDrawer();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && document.getElementById('drawer')?.classList.contains('open')) closeDrawer();
  });
}
function openDrawer() {
  document.getElementById('drawer')?.classList.add('open');
  document.getElementById('backdrop')?.classList.add('open');
  document.body.style.overflow = 'hidden';
}
function closeDrawer() {
  document.getElementById('drawer')?.classList.remove('open');
  document.getElementById('backdrop')?.classList.remove('open');
  document.body.style.overflow = '';
}

// ------------------------------
// Search / Sort
// ------------------------------
// Named debounce timers, so a keystroke in search and a keystroke elsewhere
// cannot cancel each other's pending request.
const _defer = Object.create(null);
function debounce(key, fn, ms) {
  clearTimeout(_defer[key]);
  _defer[key] = setTimeout(fn, ms);
}
// Both re-query the API. Filtering the current page in the browser would
// report a result count that contradicted what the backend would return for
// the next page, so search and sort always round-trip.
function onSearchChange(e) {
  APP_STATE.searchQuery = (e.target.value || '').trim();
  debounce('search', () => { APP_STATE.page = 1; refreshJobs(); }, 280);
}
function onSortChange(value) {
  // The API's accepted values, mapped from the human labels in the <select>.
  const map = {
    'Best match': 'score',
    'Newest': 'newest',
    'Highest salary': 'salary',
    'Most relevant': 'best_match',
  };
  APP_STATE.sortMode = map[value] || 'score';
  APP_STATE.page = 1;
  refreshJobs();
}

// ------------------------------
// Filters & Sort
// ------------------------------
function clearAllFilters() {
  APP_STATE.searchQuery = '';
  APP_STATE.sortMode = 'score';
  APP_STATE.page = 1;
  const q = document.getElementById('explore-q');
  if (q) q.value = '';
  const s = document.getElementById('sort');
  if (s) s.value = 'Best match';
  document.querySelectorAll('#parsed [data-filter]').forEach(c => c.remove());
  document.querySelectorAll('#source-filter input[type="checkbox"]').forEach(c => { c.checked = false; });
  document.querySelectorAll('#category-filter input[type="checkbox"]').forEach(c => { c.checked = false; });
  refreshJobs();
  showToast('All filters cleared', 'filter_alt_off');
}

// Placeholder cards while a request is in flight. Fixed dimensions so the
// layout does not jump when real cards replace them.
function renderSkeletons(n) {
  const one = `
    <article class="job-card p-[18px] flex flex-col gap-3" aria-hidden="true">
      <div class="flex items-center gap-3">
        <div class="w-11 h-11 rounded-xl bg-cream animate-pulse shrink-0"></div>
        <div class="flex flex-col gap-2 flex-1">
          <div class="h-3 w-1/3 rounded bg-cream animate-pulse"></div>
          <div class="h-2.5 w-1/4 rounded bg-cream animate-pulse"></div>
        </div>
      </div>
      <div class="h-5 w-3/4 rounded bg-cream animate-pulse"></div>
      <div class="h-3 w-1/2 rounded bg-cream animate-pulse"></div>
      <div class="h-4 w-1/3 rounded bg-cream animate-pulse mt-1"></div>
      <div class="flex gap-1.5 mt-1">
        <div class="h-6 w-16 rounded-full bg-cream animate-pulse"></div>
        <div class="h-6 w-20 rounded-full bg-cream animate-pulse"></div>
        <div class="h-6 w-14 rounded-full bg-cream animate-pulse"></div>
      </div>
      <div class="h-8 w-full rounded-lg bg-cream animate-pulse mt-1"></div>
    </article>`;
  return `<div class="flex flex-col gap-4" role="status" aria-live="polite" aria-label="Loading opportunities">${one.repeat(Math.max(1, n))}</div>
    <span class="sr-only">Loading opportunities…</span>`;
}

// ------------------------------
// Facets
// ------------------------------
// Both renderers build from the live registry/API. A source that is not
// monitored, or a category with no listings, is simply not offered — an
// offered filter that returns everything unchanged is worse than no filter.
function renderSourceFilter() {
  const host = document.getElementById('source-filter');
  if (!host) return;
  const monitored = APP_STATE.sources.filter(s => s.monitored);
  if (!monitored.length) {
    host.innerHTML = `<p class="text-[12px] text-cocoa-500 font-semibold m-0">No monitored sources are reporting right now.</p>`;
    return;
  }
  host.innerHTML = monitored.map(s => `
    <label class="filter-row flex items-center gap-2.5 cursor-pointer text-[13px] font-semibold text-cocoa-600">
      <input type="checkbox" class="focus-ring accent-espresso-400" value="${escapeHtml(s.id)}">
      <span class="truncate">${escapeHtml(s.name)}</span>
    </label>`).join('');
  host.querySelectorAll('input[type="checkbox"]').forEach(cb => {
    cb.addEventListener('change', () => {
      const on = [...host.querySelectorAll('input:checked')].map(i => i.value);
      APP_STATE.page = 1;
      // Repeat the parameter, exactly as the API expects a multi-value query.
      const params = { page: 1, page_size: APP_STATE.pageSize, source: on };
      if (APP_STATE.searchQuery) params.q = APP_STATE.searchQuery;
      if (APP_STATE.sortMode) params.sort = APP_STATE.sortMode;
      APP_STATE.isLoading = true;
      APP_STATE.error = null;
      renderJobFeed();
      API.jobs(params).then(data => {
        APP_STATE.jobs = data.items || [];
        APP_STATE.total = data.total || 0;
      }).catch(err => {
        APP_STATE.jobs = [];
        APP_STATE.error = err;
      }).finally(() => {
        APP_STATE.isLoading = false;
        renderJobFeed();
      });
    });
  });
}

function renderCategoryFilter() {
  const host = document.getElementById('category-filter');
  if (!host) return;
  const rows = Array.isArray(APP_STATE.categories) ? APP_STATE.categories : [];
  if (!rows.length) {
    host.innerHTML = `<p class="text-[12px] text-cocoa-500 font-semibold m-0">Categories are unavailable.</p>`;
    return;
  }
  host.innerHTML = rows.map(c => `
    <label class="filter-row flex items-center gap-2.5 cursor-pointer text-[13px] font-semibold text-cocoa-600">
      <input type="checkbox" class="focus-ring accent-espresso-400" value="${escapeHtml(c.tag || c.category || '')}">
      <span class="truncate">${escapeHtml(c.tag || c.category || '')}</span>
      <span class="ml-auto text-[11.5px] text-cocoa-400 tabular-nums">${escapeHtml(String(c.count ?? ''))}</span>
    </label>`).join('');
}

// ------------------------------
// Render Feed
// ------------------------------
function renderJobFeed() {
  const feed = document.getElementById('job-feed');
  const empty = document.getElementById('empty');
  const error = document.getElementById('error');
  if (!feed) return;

  // A fetch failure is a different screen from "nothing matched". Showing an
  // empty state for a network error tells the candidate there are no jobs
  // when the truth is that we could not ask.
  if (APP_STATE.error) {
    feed.innerHTML = '';
    empty?.classList.add('hidden');
    if (error) {
      error.classList.remove('hidden');
      const msg = document.getElementById('error-msg');
      if (msg) msg.textContent = APP_STATE.error.message || 'Could not load opportunities.';
      const retry = document.getElementById('error-retry');
      if (retry) retry.onclick = () => refreshJobs();
    }
    const c0 = document.getElementById('opp-count');
    if (c0) c0.textContent = '0';
    return;
  }
  if (error) error.classList.add('hidden');

  // The backend already applied every filter, so `total` is the true count
  // for the current query, not just what is on this page.
  const count = APP_STATE.total || APP_STATE.jobs.length;
  const oppCount = document.getElementById('opp-count');
  const resSummary = document.getElementById('res-summary');
  if (oppCount) oppCount.textContent = String(count);
  if (resSummary) resSummary.innerHTML = `<strong class="text-espresso-300 font-extrabold tabular-nums">${count}</strong> opportunities`;

  if (APP_STATE.isLoading) {
    feed.innerHTML = renderSkeletons(4);
    empty?.classList.add('hidden');
    return;
  }

  if (APP_STATE.jobs.length === 0) {
    feed.innerHTML = '';
    empty?.classList.remove('hidden');
    return;
  }
  empty?.classList.add('hidden');

  const compact = APP_STATE.viewMode === 'compact';
  feed.innerHTML = APP_STATE.jobs.map((job, idx) => renderJobCard(job, idx, compact)).join('');

  // Highlight selected
  if (APP_STATE.selectedJobId) {
    feed.querySelectorAll('[data-job-id]').forEach(c => {
      const on = c.getAttribute('data-job-id') === APP_STATE.selectedJobId
        && APP_STATE.jobs.some(j => j.slug === APP_STATE.selectedJobId);
      c.classList.toggle('is-selected', on);
    });
  }

  // Bind card events
  feed.querySelectorAll('[data-job-id]').forEach(card => {
    const id = card.getAttribute('data-job-id');
    const job = APP_STATE.jobs.find(j => j.slug === id);
    if (!job) return;

    card.addEventListener('click', (e) => {
      if (e.target.closest('button') || e.target.closest('a')) return;
      if (window.matchMedia('(min-width: 1536px)').matches) {
        selectJobForPreview(id);
      } else {
        navigateToJobDetail(id);
      }
    });
    card.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); navigateToJobDetail(id); }
    });

    card.querySelectorAll('.save-btn').forEach(btn => {
      btn.addEventListener('click', (e) => { e.stopPropagation(); toggleSave(id, btn); });
    });
    card.querySelectorAll('.view-job').forEach(el => {
      el.addEventListener('click', (e) => { e.stopPropagation(); e.preventDefault(); navigateToJobDetail(id); });
    });
  });
}

function setViewMode(mode) {
  APP_STATE.viewMode = mode;
  const setPress = (btn, pressed) => {
    if (!btn) return;
    if (pressed) { btn.classList.add('bg-cream','text-espresso-300','hairline'); btn.classList.remove('text-cocoa-400'); }
    else { btn.classList.remove('bg-cream','text-espresso-300','hairline'); btn.classList.add('text-cocoa-400','hover:bg-cream'); }
    btn.setAttribute('aria-pressed', pressed ? 'true' : 'false');
  };
  setPress(document.getElementById('view-list'), mode === 'list');
  setPress(document.getElementById('view-compact'), mode === 'compact');
  renderJobFeed();
}

// ------------------------------
// Job Card
// ------------------------------
// Rendered strictly from the API's JobSummary fields. Anything the source did
// not publish is shown as absent — there is no client-side inference of
// match scores, eligibility, quality or salary.
function renderJobCard(job, index, compact) {
  const initials = (job.company || '??').replace(/[^A-Za-z0-9\s]/g,'').split(/\s+/).filter(Boolean).slice(0,2).map(s => s[0]).join('').toUpperCase();
  const logoBg = colorForCompany(job.company);
  const salaryHtml = formatSalary(job);
  const sourceBadgeHtml = renderSourceBadge(job);
  const freshHtml = renderFreshness(job);
  const skillsHtml = renderSkills(job.tags || [], 6);
  const saved = APP_STATE.savedIds.has(job.slug);
  const sourceName = (job.source_info && job.source_info.name) || job.source || 'Unknown source';
  const detailHref = `job-detail.html?slug=${encodeURIComponent(job.slug)}`;

  return `
    <article
      data-job-id="${escapeHtml(job.slug)}"
      tabindex="0"
      aria-label="${escapeHtml(job.title)} at ${escapeHtml(job.company)}"
      class="job-card p-[18px] flex flex-col gap-3 animate-pop"
      style="animation-delay:${Math.min(index*28, 280)}ms"
    >
      <!-- TOP ROW -->
      <div class="flex items-start justify-between gap-3">
        <div class="flex items-center gap-3 min-w-0">
          <div class="w-11 h-11 rounded-xl grid place-items-center font-extrabold text-ivory shrink-0 shadow-card text-[14px]" style="background:${logoBg};">
            ${initials}
          </div>
          <div class="flex flex-col min-w-0">
            <div class="flex items-center gap-1.5 flex-wrap">
              <span class="text-[13.5px] font-bold text-espresso-400 truncate">${escapeHtml(job.company)}</span>
              <span class="hr-dot text-cocoa-300"></span>
              ${sourceBadgeHtml}
            </div>
            <div class="flex items-center gap-1 mt-0.5 text-[11.5px] text-cocoa-500 font-semibold">
              ${freshHtml}
            </div>
          </div>
        </div>
        <div class="flex items-center gap-1 shrink-0">
          <button class="save-btn icon-btn focus-ring ${saved ? 'is-saved' : ''}" type="button" data-job="${escapeHtml(job.slug)}" aria-label="${saved ? 'Unsave job' : 'Save job'}">
            <span class="material-symbols-rounded ${saved ? 'ic-fill' : ''}" style="font-size:19px;">${saved ? 'bookmark' : 'bookmark_border'}</span>
          </button>
        </div>
      </div>

      <!-- TITLE + METADATA -->
      <div class="min-w-0">
        <a href="${detailHref}" class="view-job no-underline block">
          <h2 class="m-0 font-display text-[22px] md:text-[24px] leading-[1.04] text-espresso-400 italic hover:text-gold transition-colors tracking-tight">
            ${escapeHtml(job.title)}
          </h2>
        </a>
        <div class="flex flex-wrap items-center gap-y-1 gap-x-3 mt-2 text-[12.5px] text-cocoa-600 font-semibold">
          <span class="inline-flex items-center gap-1">
            <span class="material-symbols-rounded text-cocoa-400" style="font-size:15px;">${job.remote ? 'public' : 'location_city'}</span>
            ${escapeHtml(job.location_label || 'Location not specified')}
          </span>
          ${job.is_ai_related ? `<span class="chip bg-champagne-50 text-gold hairline-gold !text-[10.5px] font-extrabold">AI work</span>` : ''}
        </div>
      </div>

      <!-- SALARY -->
      <div class="flex items-baseline gap-2 flex-wrap">
        ${salaryHtml}
      </div>

      ${compact ? '' : `
      <!-- TAGS / SKILLS -->
      <div class="flex flex-wrap items-center gap-1.5">
        ${skillsHtml}
      </div>
      `}

      <!-- ACTIONS -->
      <div class="flex items-center justify-between gap-3 pt-1 mt-auto">
        <div class="flex items-center gap-1.5 min-w-0 text-[11.5px] text-cocoa-500 truncate">
          <span class="material-symbols-rounded" style="font-size:14px;">source</span>
          <span class="truncate">Source: <strong class="text-espresso-300">${escapeHtml(sourceName)}</strong></span>
        </div>
        <div class="flex items-center gap-2 shrink-0">
          <a href="${detailHref}"
             class="view-job btn btn-primary focus-ring !py-1.5 !px-3 sm:!px-4 !text-[12.5px]"
             data-job="${escapeHtml(job.slug)}"
             aria-label="View full details for ${escapeHtml(job.title)}"
          >
            <span>View opportunity</span>
            <span class="material-symbols-rounded" style="font-size:15px;">arrow_forward</span>
          </a>
        </div>
      </div>
    </article>
  `;
}

// ------------------------------
// Freshness
// ------------------------------
// The API computes age and staleness; this only formats it. A listing with no
// published date is reported as such rather than being aged from crawl time.
function renderFreshness(job) {
  const f = job.freshness || {};
  if (!f.discovered_at) {
    return `<span class="material-symbols-rounded" style="font-size:13px;">help_outline</span><span>No date published</span>`;
  }
  const label = f.label || '';
  if (f.is_stale) {
    return `<span class="material-symbols-rounded" style="font-size:13px;">history</span><span>${escapeHtml(label)} · verify before applying</span>`;
  }
  return `<span class="material-symbols-rounded" style="font-size:13px;">schedule</span><span>${escapeHtml(label)}</span>`;
}


// ------------------------------
// Preview Panel
// ------------------------------
function selectJobForPreview(jobId, opts = {}) {
  APP_STATE.selectedJobId = jobId;
  document.querySelectorAll('#job-feed [data-job-id]').forEach(c => {
    c.classList.toggle('is-selected', c.getAttribute('data-job-id') === jobId);
  });
  const job = APP_STATE.jobs.find(j => j.slug === jobId);
  if (job) renderQuickPreview(job);
}
function renderQuickPreview(job) {
  const container = document.getElementById('preview');
  if (!container) return;
  const initials = (job.company || '??').split(/\s+/).filter(Boolean).slice(0,2).map(s => s[0]).join('').toUpperCase();
  const tags = (job.tags || []).slice(0, 3);
  const saved = APP_STATE.savedIds.has(job.slug);
  const sourceName = (job.source_info && job.source_info.name) || job.source || 'Unknown source';
  const detailHref = `job-detail.html?slug=${encodeURIComponent(job.slug)}`;

  container.innerHTML = `
    <div class="rounded-2xl bg-white hairline-strong shadow-card p-5 flex flex-col gap-4 animate-pop">
      <div class="flex items-center justify-between pb-3 border-b border-cocoa/10">
        <div class="flex items-center gap-2">
          <span class="material-symbols-rounded text-gold" style="font-size:18px;">insights</span>
          <span class="text-[13.5px] font-extrabold tracking-tight text-espresso-400">Quick preview</span>
        </div>
      </div>
      <div class="flex items-center gap-3">
        <div class="w-12 h-12 rounded-xl grid place-items-center text-ivory font-extrabold shrink-0 shadow-card" style="background:${colorForCompany(job.company)};">${initials}</div>
        <div class="flex flex-col min-w-0">
          <span class="text-[13.5px] font-extrabold text-espresso-400 truncate">${escapeHtml(job.company)}</span>
          <span class="text-[11.5px] text-cocoa-500 truncate font-semibold">${escapeHtml(sourceName)} · ${escapeHtml((job.freshness && job.freshness.label) || 'date not published')}</span>
        </div>
      </div>
      <div class="p-3.5 rounded-xl bg-gradient-to-br from-cream to-cream-dark hairline flex flex-col gap-1.5">
        <h3 class="m-0 font-display text-[18px] leading-snug text-espresso-400 italic">${escapeHtml(job.title)}</h3>
        <div class="mt-1 text-[15px] font-extrabold tabular-nums text-espresso-400">${formatSalaryInline(job)}</div>
        <div class="mt-0.5 text-[12px] text-cocoa-600 flex items-center gap-1 font-semibold">
          <span class="material-symbols-rounded" style="font-size:13px;">${job.remote ? 'public' : 'location_city'}</span>
          ${escapeHtml(job.location_label || 'Location not specified')}
        </div>
      </div>
      <div class="flex flex-col gap-2">
        <div class="flex flex-wrap gap-1.5">
          ${tags.length ? tags.map(s => `<span class="pill bg-espresso-300 text-ivory font-bold text-[11.5px] !py-1 !px-2">${escapeHtml(s)}</span>`).join('')
            : `<span class="text-[12px] text-cocoa-500 font-semibold">No tags published</span>`}
        </div>
      </div>
      <div class="flex flex-col gap-2 pt-1 border-t border-cocoa/10">
        <a href="${detailHref}" class="view-job btn btn-primary w-full justify-center focus-ring" data-job="${escapeHtml(job.slug)}">
          <span>View full opportunity</span><span class="material-symbols-rounded" style="font-size:15px;">arrow_forward</span>
        </a>
        <button class="save-btn btn w-full justify-center !text-[12.5px] focus-ring" type="button" data-job="${escapeHtml(job.slug)}">
          <span class="material-symbols-rounded ${saved ? 'ic-fill text-gold' : ''}" style="font-size:17px;">${saved ? 'bookmark' : 'bookmark_border'}</span>
          <span>${saved ? 'Saved' : 'Save opportunity'}</span>
        </button>
      </div>
    </div>
  `;

  container.querySelectorAll('.view-job').forEach(a => {
    a.addEventListener('click', (e) => {
      e.preventDefault();
      const id = a.getAttribute('data-job');
      if (id) navigateToJobDetail(id);
    });
  });
  container.querySelectorAll('.save-btn').forEach(btn => {
    btn.addEventListener('click', () => toggleSave(job.slug, btn));
  });
}

// ------------------------------
// Save
// ------------------------------
function toggleSave(jobId, sourceEl) {
  const saved = APP_STATE.savedIds.has(jobId);
  if (saved) { APP_STATE.savedIds.delete(jobId); showToast('Removed from saved', 'bookmark_border'); }
  else { APP_STATE.savedIds.add(jobId); showToast('Saved to your pipeline', 'bookmark'); }

  // Micro-pop on source
  if (sourceEl) {
    sourceEl.animate([{transform:'scale(1)'},{transform:'scale(1.12)', offset:0.4},{transform:'scale(1)'}],{duration:260, easing:'cubic-bezier(.2,.8,.2,1)'});
  }
  // Refresh card icons
  document.querySelectorAll(`[data-job-id="${jobId}"] .save-btn`).forEach(btn => {
    const nowSaved = APP_STATE.savedIds.has(jobId);
    const icon = btn.querySelector('.material-symbols-rounded');
    btn.classList.toggle('is-saved', nowSaved);
    if (icon) { icon.classList.toggle('ic-fill', nowSaved); icon.textContent = nowSaved ? 'bookmark' : 'bookmark_border'; }
  });
  if (APP_STATE.selectedJobId === jobId) {
    const job = APP_STATE.jobs.find(j => j.job_id === jobId);
    if (job) renderQuickPreview(job);
  }
}

// ------------------------------
// Navigate to Detail
// ------------------------------
function navigateToJobDetail(jobId) {
  document.body.style.transition = 'opacity 180ms ease';
  document.body.style.opacity = '0.7';
  setTimeout(() => { window.location.href = `job-detail.html?id=${encodeURIComponent(jobId)}`; }, 110);
}

// ------------------------------
// Job Detail (stub)
// ------------------------------
function initJobDetailPage() {
  const params = new URLSearchParams(window.location.search);
  const id = params.get('id') || 'job_001';
  const titleEl = document.getElementById('jd-title');
  const backLink = document.getElementById('jd-back');
  backLink?.addEventListener('click', (e) => {
    e.preventDefault();
    window.history.length > 1 ? window.history.back() : (window.location.href = 'opportunities.html');
  });
  if (titleEl) {
    titleEl.textContent = `Loading job ${id}…`;
    setTimeout(() => {
      const job = (typeof MOCK_JOBS !== 'undefined') ? MOCK_JOBS.find(j => j.job_id === id) : null;
      if (job) titleEl.textContent = job.title;
    }, 120);
  }
}

// ------------------------------
// Toast
// ------------------------------
function showToast(message, icon = 'check_circle') {
  const t = document.getElementById('toast');
  const tText = document.getElementById('toast-text');
  const tIcon = document.getElementById('toast-icon');
  if (!t) return;
  if (tText) tText.textContent = message;
  if (tIcon) tIcon.textContent = icon;
  t.classList.add('show');
  clearTimeout(t._tid);
  t._tid = setTimeout(() => t.classList.remove('show'), 2300);
}

// ------------------------------
// Rendering helpers
// ------------------------------
function renderSkills(skills, max = 6) {
  if (!skills || skills.length === 0) return `<span class="text-[12px] text-cocoa-400 font-semibold">No skills listed</span>`;
  const shown = skills.slice(0, max);
  const rest = skills.length - max;
  return shown.map((s, i) => `<span class="pill ${i < 2 ? 'bg-espresso-300 text-ivory border border-transparent' : 'bg-ivory text-cocoa-600 hairline-strong hover:bg-cream'} font-bold text-[11.5px] !py-1 !px-2.5">${escapeHtml(s)}</span>`).join('')
    + (rest > 0 ? `<span class="pill bg-cream text-cocoa-500 hairline font-extrabold !py-1 !px-2 !text-[11px]">+${rest} more</span>` : '');
}
function renderSourceBadge(job) {
  if (job.sources && job.sources.length > 1) {
    return `<span class="chip bg-white hairline text-cocoa-500 font-bold !text-[10.5px]" title="This opportunity was found across ${job.sources.length} sources: ${job.sources.join(', ')}">
      <span class="material-symbols-rounded" style="font-size:12px;">merge_type</span>${job.sources.length} sources
    </span>`;
  }
  return `<span class="chip bg-white hairline text-cocoa-500 font-semibold !text-[10.5px]" title="Source: ${escapeHtml(job.source || '')}">via ${escapeHtml(job.source || 'Unknown')}</span>`;
}
function renderMatchBreakdown(job) {
  const mb = job.match_breakdown || {};
  return `
    <div class="inline-flex items-center gap-3">
      <span class="text-cocoa-500 font-semibold">Skills <strong class="text-espresso-400 tabular-nums">${mb.skills ?? '—'}%</strong></span>
      <span class="text-cocoa-500 font-semibold">Experience <strong class="text-espresso-400 tabular-nums">${mb.experience ?? '—'}%</strong></span>
      <span class="text-cocoa-500 font-semibold">Eligibility <strong class="text-espresso-400 tabular-nums">${mb.eligibility ?? '—'}%</strong></span>
    </div>
  `;
}
function matchScoreBadge(score) {
  if (score == null || isNaN(score)) {
    return `<span class="chip bg-cream text-cocoa-500 font-bold flex items-center gap-1 !text-[11px]">
      <span class="w-1.5 h-1.5 rounded-full bg-cocoa-300 animate-pulse"></span>Analyzing
    </span>`;
  }
  return `<span class="chip ${matchCssClass(score)} font-extrabold flex items-center gap-1 !text-[11.5px] tabular-nums" title="${matchLabel(score)} overall match">
    <span class="w-1.5 h-1.5 rounded-full bg-current"></span>${score}%
  </span>`;
}
function matchLabel(s) {
  if (s == null) return 'Analyzing';
  if (s >= 90) return 'Excellent fit';
  if (s >= 75) return 'Great fit';
  if (s >= 60) return 'Good fit';
  return 'Partial match';
}
function renderEligibility(status) {
  const map = {
    'eligible':     { dot:'bg-success', text:'text-success', label:'Eligible', tip:'You meet explicit location, experience, and work eligibility requirements.' },
    'review':       { dot:'bg-warning', text:'text-warning', label:'Review required', tip:'Some constraints need manual review — may still apply.' },
    'not_eligible': { dot:'bg-danger',  text:'text-danger',  label:'Not eligible',    tip:'Mismatch on location, experience level, or core constraints.' }
  };
  const s = map[status] || map.review;
  return `<span class="inline-flex items-center gap-1.5 ${s.text}" title="${s.tip}" role="status">
    <span class="sr-only">Eligibility: </span>
    <span class="dot ${s.dot} ${status === 'eligible' ? 'live-dot' : ''}"></span><strong>${s.label}</strong>
  </span>`;
}
function renderQuality(score) {
  if (!score) return '';
  const label = ({'Excellent':'Excellent','Good':'Good','Review':'Review'})[score] || 'Good';
  const cls = ({'Excellent':'bg-success-soft text-success hairline','Good':'bg-info-soft text-info hairline','Review':'bg-warning-soft text-warning hairline'})[score] || '';
  return `<span class="chip ${cls} !text-[10.5px] uppercase tracking-[0.12em] font-extrabold" title="DEDAN job quality: ${label}">Quality · ${label}</span>`;
}

// ------------------------------
// Formatting
// ------------------------------
function formatSalary(job) {
  if (!job.salary) {
    // The backend flags whether a salary was actually published. When it was
    // not, the honest card says so. It used to invent a range from the
    // experience level and label it "DEDAN Est." — a fabricated number the
    // candidate could reasonably mistake for the employer's own.
    return `<span class="text-[15px] font-extrabold text-cocoa-500">Salary not disclosed by the source</span>`;
  }
  return `<span class="text-[19px] md:text-[20px] font-extrabold text-espresso-400 tracking-tight tabular-nums">${escapeHtml(job.salary)}</span>`;
}
function formatSalaryInline(job) {
  return job.salary || 'Salary not disclosed';
}
function formatCompactNumber(n) {
  if (n == null) return '—';
  if (n >= 1000) return (n/1000) + 'k';
  return String(n);
}
function matchColor(s) {
  if (s == null) return '#8E7A5E';
  if (s >= 90) return '#2F6D51';
  if (s >= 75) return '#275E8C';
  if (s >= 60) return '#A87016';
  return '#992F2A';
}
function matchCssClass(s) {
  if (s == null) return 'bg-cream text-cocoa-500';
  if (s >= 90) return 'bg-success-soft text-success';
  if (s >= 75) return 'bg-info-soft text-info';
  if (s >= 60) return 'bg-warning-soft text-warning';
  return 'bg-danger-soft text-danger';
}
function timeAgo(iso) {
  if (!iso) return '';
  const diff = Date.now() - new Date(iso).getTime();
  const s = Math.max(1, Math.floor(diff / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}${m === 1 ? ' min' : 'm'} ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d} day${d === 1 ? '' : 's'} ago`;
  const mo = Math.floor(d / 30);
  return `${mo}mo ago`;
}
function colorForCompany(name) {
  const palette = [
    'linear-gradient(135deg,#1C1610 0%,#514330 100%)',
    'linear-gradient(135deg,#6C5A42 0%,#A87F36 100%)',
    'linear-gradient(135deg,#2F6D51 0%,#1C3A2C 100%)',
    'linear-gradient(135deg,#275E8C 0%,#163A55 100%)',
    'linear-gradient(135deg,#A87016 0%,#5C3F0D 100%)',
    'linear-gradient(135deg,#8E7A5E 0%,#C89A47 100%)',
    'linear-gradient(135deg,#3A2F22 0%,#6C5A42 100%)',
    'linear-gradient(135deg,#5C3F0D 0%,#A87F36 100%)',
    'linear-gradient(135deg,#1C3A2C 0%,#4B8C74 100%)'
  ];
  let h = 0;
  const n = name || '';
  for (let i = 0; i < n.length; i++) { h = ((h * 31 + n.charCodeAt(i)) >>> 0); }
  return palette[h % palette.length];
}
function escapeHtml(str) {
  if (str == null) return '';
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}
