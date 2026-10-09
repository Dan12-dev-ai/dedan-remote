// ============================================================
// DEDAN Remote — Theme engine (light / dark)
// ------------------------------------------------------------
// Drives a class-based dark mode using CSS custom properties so every page
// can share one palette without a build step. Load FIRST (before the page's
// <style>) so there is no flash of the wrong theme.
//
//   <script src="theme.js"></script>
//
// Dark palette (spec):
//   bg obsidian   #0B0F17    surfaces #131B2E    borders white/10
//   accent gold   #E2B857    text #F8FAFC / muted #94A3B8
// ============================================================

(function () {
  const KEY = 'dedan_theme';
  const root = document.documentElement;

  function systemPrefersDark() {
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  }

  function stored() {
    try { return localStorage.getItem(KEY); } catch (e) { return null; }
  }

  function apply(mode) {
    const dark = mode === 'dark';
    root.classList.toggle('dark', dark);
    root.setAttribute('data-theme', dark ? 'dark' : 'light');
    // Keep the browser UI (form controls, scrollbars) in sync.
    root.style.colorScheme = dark ? 'dark' : 'light';
    // Notify any listeners (e.g. icon swap) that the theme changed.
    document.dispatchEvent(new CustomEvent('themechange', { detail: { dark } }));
  }

  function current() {
    return root.classList.contains('dark') ? 'dark' : 'light';
  }

  function set(mode, persist) {
    apply(mode);
    if (persist !== false) { try { localStorage.setItem(KEY, mode); } catch (e) {} }
  }

  function toggle() {
    set(current() === 'dark' ? 'light' : 'dark');
  }

  // Resolve the initial theme before paint: explicit choice wins, else system.
  const saved = stored();
  const initial = saved === 'dark' || saved === 'light' ? saved : (systemPrefersDark() ? 'dark' : 'light');
  root.classList.toggle('dark', initial === 'dark');
  root.setAttribute('data-theme', initial);
  root.style.colorScheme = initial === 'dark' ? 'dark' : 'light';

  // Follow the OS only while the user has not made an explicit choice.
  if (!saved && window.matchMedia) {
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = (e) => { if (!stored()) set(e.matches ? 'dark' : 'light', false); };
    mq.addEventListener ? mq.addEventListener('change', onChange) : mq.addListener(onChange);
  }

  // Public API
  window.DEDAN_THEME = { set, toggle, current, isDark: () => current() === 'dark' };
})();
