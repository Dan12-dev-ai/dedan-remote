// ============================================================
// DEDAN Remote — shared authenticated chrome
// ------------------------------------------------------------
// Injects the top header (brand, primary nav, session avatar, sign-out)
// into any element carrying [data-app-header]. Keeps the header markup in
// one place instead of duplicating it across every authenticated page.
//
// Load AFTER shared.js (needs window.API) and BEFORE the page script.
// ============================================================

(function () {
  function esc(v) {
    return String(v === null || v === undefined ? '' : v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function initials(name) {
    if (!name) return '··';
    const parts = String(name).trim().split(/[\s@.]+/).filter(Boolean);
    return ((parts[0] || '')[0] + (parts[1] ? parts[1][0] : '')).toUpperCase() || '··';
  }

  function navItem(href, icon, label, active) {
    return '<a href="' + href + '" class="nav-link' + (active ? ' is-active' : '') + '">' +
      '<span class="material-symbols-rounded" style="font-size:17px;">' + icon + '</span>' + esc(label) + '</a>';
  }

  function headerHTML(active, userLabel) {
    return '' +
      '<header class="sticky top-0 z-40 border-b" style="background:var(--header-bg); backdrop-filter:blur(14px); border-color:var(--border);">' +
      '  <div class="w-full h-16 flex items-center justify-between pl-6 md:pl-10 pr-6 md:pr-10 gap-4">' +
      // Left edge: brand
      '    <a href="index.html" class="flex items-center gap-2.5 shrink-0 focus-ring rounded-lg" aria-label="DEDAN Remote · Home">' +
      '      <div class="relative w-9 h-9 rounded-xl grid place-items-center overflow-hidden" style="background:linear-gradient(135deg,#231C14,#514330); color:#FBF7F0;">' +
      '        <span class="font-serif text-[18px] italic font-semibold tracking-tight">D</span>' +
      '        <span class="absolute -bottom-1 -right-1 w-2.5 h-2.5 rounded-full" style="background:var(--accent); box-shadow:0 0 0 2px var(--bg);"></span>' +
      '      </div>' +
      '      <div class="flex flex-col leading-tight hidden sm:flex">' +
      '        <span class="text-[14.5px] font-extrabold tracking-tight tk-text">DEDAN Remote</span>' +
      '        <span class="text-[10px] uppercase tracking-[0.14em] font-semibold tk-faint -mt-0.5">Opportunity Intelligence</span>' +
      '      </div>' +
      '    </a>' +
      // Right edge: unified action cluster (search · theme · context CTA · avatar)
      '    <div class="flex items-center gap-2">' +
      '      <button id="chrome-search" class="btn btn-ghost !px-3" type="button" title="Search (⌘K)" aria-label="Search">' +
      '        <span class="material-symbols-rounded" style="font-size:19px;">search</span>' +
      '        <span class="hidden lg:inline kbd tk-faint">⌘K</span>' +
      '      </button>' +
      '      <button id="chrome-theme" class="btn btn-ghost !px-3" type="button" title="Toggle theme" aria-label="Toggle dark mode">' +
      '        <span id="chrome-theme-icon" class="material-symbols-rounded" style="font-size:19px;">dark_mode</span>' +
      '      </button>' +
      (active === 'explore'
        ? '      <a href="opportunities.html" class="btn btn-primary !py-2">Explore</a>'
        : '') +
      (API.isSignedIn()
        ? '      <a href="profile.html" class="flex items-center gap-2 pl-1 pr-2.5 h-10 rounded-xl transition-colors focus-ring" style="background:rgba(127,127,127,.06);">' +
          '        <div id="chrome-avatar" class="w-8 h-8 rounded-full grid place-items-center text-[12px] font-extrabold" style="background:linear-gradient(135deg,#DDB767,#B08A3E); color:#1C1610;">' + esc(initials(userLabel)) + '</div>' +
          '        <span id="chrome-name" class="hidden sm:block text-[12.5px] font-bold tk-text">' + esc(userLabel || 'Account') + '</span>' +
          '      </a>' +
          '      <button id="chrome-logout" class="btn btn-ghost !px-3" type="button" title="Sign out" aria-label="Sign out">' +
          '        <span class="material-symbols-rounded" style="font-size:19px;">logout</span>' +
          '      </button>'
        : '      <a href="signin.html" class="btn btn-primary !py-2">Sign In</a>') +
      '    </div>' +
      '  </div>' +
      '</header>';
  }

  function mount() {
    const host = document.querySelector('[data-app-header]');
    if (!host) return;
    const active = host.getAttribute('data-active') || '';
    // Best-effort label from the cached user; refined after /auth/me resolves.
    let label = '';
    try { const u = API.user(); if (u) label = u.display_name || u.email; } catch (e) {}

    // IMPORTANT: the host is often the <body> element itself (pages carry
    // data-app-header on <body>). Assigning body.outerHTML would REPLACE the
    // entire document body and destroy every other element and script. So we
    // always inject the header as the FIRST CHILD of the host and remove the
    // marker attribute — never overwrite the host node.
    const wrap = document.createElement('div');
    wrap.innerHTML = headerHTML(active, label);
    const headerEl = wrap.firstElementChild;
    host.removeAttribute('data-app-header');
    host.insertBefore(headerEl, host.firstChild);

    const out = document.getElementById('chrome-logout');
    if (out) out.addEventListener('click', async () => {
      await API.logout();
      toast('Signed out.');
      setTimeout(() => location.replace('index.html'), 500);
    });

    // Theme toggle (Module 3) — swaps icon and persists via theme.js.
    const themeBtn = document.getElementById('chrome-theme');
    const themeIcon = document.getElementById('chrome-theme-icon');
    const syncThemeIcon = () => {
      const dark = window.DEDAN_THEME && window.DEDAN_THEME.isDark();
      if (themeIcon) themeIcon.textContent = dark ? 'light_mode' : 'dark_mode';
    };
    syncThemeIcon();
    document.addEventListener('themechange', syncThemeIcon);
    if (themeBtn) themeBtn.addEventListener('click', () => {
      window.DEDAN_THEME && window.DEDAN_THEME.toggle();
    });

    // Search trigger — focus the page's search box if present, else go explore.
    const searchBtn = document.getElementById('chrome-search');
    if (searchBtn) searchBtn.addEventListener('click', () => {
      const q = document.getElementById('explore-q') || document.getElementById('nav-q') || document.getElementById('q');
      if (q) { q.focus(); q.select && q.select(); }
      else location.href = 'opportunities.html';
    });
    // ⌘K / Ctrl-K global shortcut.
    document.addEventListener('keydown', (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        const q = document.getElementById('explore-q') || document.getElementById('nav-q') || document.getElementById('q');
        if (q) { q.focus(); q.select && q.select(); }
        else location.href = 'opportunities.html';
      }
    });

    // Page-specific secondary pill nav (Module 2) for complex pages.
    const subnav = document.querySelector('[data-subnav]');
    if (subnav) mountSubnav(subnav);

    // Refresh the avatar label with authoritative server data.
    if (API.isSignedIn()) {
      API.me().then(u => {
        const av = document.getElementById('chrome-avatar');
        const nm = document.getElementById('chrome-name');
        if (u) {
          const name = u.display_name || u.email || 'Account';
          if (nm) nm.textContent = name;
          if (av) av.textContent = initials(name);
        }
      }).catch(() => {});
    }

    // Enable smooth theme cross-fade now that the header is in place.
    document.documentElement.classList.add('theme-ready');
  }

  // Secondary pill navbar beneath the header, rendered from a data-driven list
  // so the top header stays clean. Items come from the host element's
  // data-subnav="label:href,label:href" attribute.
  function mountSubnav(host) {
    const spec = host.getAttribute('data-subnav') || '';
    const current = location.pathname.split('/').pop() || 'index.html';
    const items = spec.split(',').map(pair => {
      const [label, href] = pair.split(':');
      return { label: (label || '').trim(), href: (href || '').trim() };
    }).filter(i => i.label && i.href);
    if (!items.length) return;
    host.className = 'w-full flex items-center gap-1.5 px-6 md:px-10 py-2 border-b overflow-x-auto scroll-x tk-muted';
    host.style.background = 'var(--bg-sunken)';
    host.style.borderColor = 'var(--border)';
    host.innerHTML = items.map(i => {
      const on = i.href === current;
      return '<a href="' + esc(i.href) + '" class="pill-nav' + (on ? ' is-active' : '') + '">' + esc(i.label) + '</a>';
    }).join('');
    const style = document.createElement('style');
    style.textContent =
      '.pill-nav{display:inline-flex;align-items:center;padding:6px 14px;border-radius:9999px;font-size:13px;font-weight:600;color:var(--text-muted);text-decoration:none;white-space:nowrap;transition:all .15s ease;}' +
      '.pill-nav:hover{background:rgba(127,127,127,.10);color:var(--text);}' +
      '.pill-nav.is-active{background:var(--accent);color:var(--accent-contrast);}';
    host.appendChild(style);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }
})();
