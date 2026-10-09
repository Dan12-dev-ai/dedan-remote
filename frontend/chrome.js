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
      '<header class="sticky top-0 z-40 bg-[#FBF7F0]/80 backdrop-blur-xl border-b border-cocoa/10">' +
      '  <div class="grid-12 h-16 flex items-center gap-4">' +
      '    <a href="index.html" class="flex items-center gap-2.5 shrink-0 focus-ring rounded-lg" aria-label="DEDAN Remote · Home">' +
      '      <div class="relative w-9 h-9 rounded-xl bg-gradient-to-br from-espresso-300 to-espresso-500 text-ivory shadow-card grid place-items-center overflow-hidden">' +
      '        <span class="font-serif text-[18px] italic font-semibold tracking-tight relative -top-0.5">D</span>' +
      '        <span class="absolute -bottom-1 -right-1 w-2.5 h-2.5 rounded-full bg-champagne-300 ring-2 ring-[#FBF7F0]"></span>' +
      '      </div>' +
      '      <div class="flex flex-col leading-tight hidden sm:flex">' +
      '        <span class="text-[14.5px] font-extrabold tracking-tight text-espresso-300">DEDAN Remote</span>' +
      '        <span class="text-[10px] uppercase tracking-[0.14em] font-semibold text-cocoa-400 -mt-0.5">Opportunity Intelligence</span>' +
      '      </div>' +
      '    </a>' +
      '    <nav class="hidden lg:flex items-center gap-1 ml-4" aria-label="Primary">' +
             navItem('dashboard.html', 'dashboard', 'Dashboard', active === 'dashboard') +
             navItem('opportunities.html', 'explore', 'Explore', active === 'explore') +
             navItem('saved.html', 'bookmarks', 'Saved', active === 'saved') +
             navItem('applications.html', 'briefcase', 'Applications', active === 'applications') +
             navItem('interview.html', 'forum', 'Interviews', active === 'interviews') +
      '    </nav>' +
      '    <div class="ml-auto flex items-center gap-2">' +
      '      <a href="profile.html" class="flex items-center gap-2 pl-1 pr-2.5 h-10 rounded-xl hover:bg-cream transition-colors focus-ring">' +
      '        <div id="chrome-avatar" class="w-8 h-8 rounded-full bg-gradient-to-br from-champagne-300 to-gold grid place-items-center text-espresso-300 text-[12px] font-extrabold shadow-card ring-1 ring-champagne-400/40">' + esc(initials(userLabel)) + '</div>' +
      '        <span id="chrome-name" class="hidden sm:block text-[12.5px] font-bold text-espresso-300">' + esc(userLabel || 'Account') + '</span>' +
      '      </a>' +
      '      <button id="chrome-logout" class="btn" type="button" title="Sign out">' +
      '        <span class="material-symbols-rounded" style="font-size:17px;">logout</span><span class="hidden sm:inline">Sign out</span>' +
      '      </button>' +
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
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }
})();
