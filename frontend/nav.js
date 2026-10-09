// ============================================================
// DEDAN Remote — client-side navigation router
// ------------------------------------------------------------
// The site is a static multi-page app served by FastAPI. Every page
// previously used `href="#"` plus an unbound `data-path` attribute, so
// Sign In, Explore Opportunities, job cards and the footer links were all
// dead. This module gives every `data-path` a real destination and wires the
// links once, on any page that loads it.
//
// Route table (single source of truth):
//   path key        -> url (relative to the site root, always the .html file)
// Job detail is dynamic: routeJob(id) -> job-detail.html?id=<id>
// ============================================================

const ROUTES = {
  // marketing / landing anchors
  'overview':            'index.html',
  'home':                'index.html',
  'how-it-works':        'index.html#how-it-works',
  'ecosystem':           'index.html',
  'product':             'index.html',
  'pricing':             'index.html',
  'intelligence-engine': 'index.html',
  'intelligence-dossier':'index.html',
  'radar-benchmarks':    'index.html',
  'system-architecture': 'index.html',

  // core app
  'explore':             'opportunities.html',
  'explore-opportunities':'opportunities.html',
  'saved':               'saved.html',
  'applications':        'applications.html',
  'interviews':          'interview.html',
  'ai-mock-interview':   'interview.html',
  'profile':             'profile.html',
  'job-detail':          'job-detail.html',   // usually built via routeJob()

  // auth
  'sign-in':             'signin.html',
  'signup':              'signup.html',

  // misc / marketing content pages
  'about':               'about.html',
  'manifesto':           'about.html#manifesto',
  'research':            'about.html#research',
  'careers':             'about.html#careers',
  'privacy-policy':      'legal.html#privacy',
  'terms-of-service':    'legal.html#terms',
  'security-disclosures':'legal.html#security',
};

// Resolve a data-path key to a concrete URL. Unknown keys fall back to the
// landing page rather than a dead "#" so a typo never produces a no-op link.
function route(key) {
  return ROUTES[key] || 'index.html';
}

// Dynamic job-detail route. Accepts the job id (preferred) or slug.
function routeJob(idOrSlug) {
  if (!idOrSlug) return route('explore');
  // The detail page reads both ?id= and ?slug=; prefer id.
  const q = /^[0-9a-f]{8,}$/.test(idOrSlug) ? 'id' : 'slug';
  return 'job-detail.html?' + q + '=' + encodeURIComponent(idOrSlug);
}

// Wire every element carrying a data-path attribute to its route.
// Safe to call on every page; idempotent.
function wireNavigation() {
  document.querySelectorAll('[data-path]').forEach(el => {
    const key = el.getAttribute('data-path');
    const href = route(key);
    if (el.tagName === 'A') {
      el.setAttribute('href', href);
    } else {
      el.setAttribute('role', 'link');
      el.setAttribute('tabindex', '0');
      el.style.cursor = 'pointer';
      const go = () => { window.location.href = href; };
      el.addEventListener('click', go);
      el.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
    }
  });
}

// Convenience: navigate programmatically.
function go(key) { window.location.href = route(key); }
function goJob(idOrSlug) { window.location.href = routeJob(idOrSlug); }

// Auto-wire on DOM ready (loaded after the DOM in the page footer).
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', wireNavigation);
} else {
  wireNavigation();
}
