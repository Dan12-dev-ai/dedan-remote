/**
 * Motion system for DEDAN Remote.
 *
 * Design rules this module enforces:
 *  - Nothing animates unless the user's OS allows it. `prefers-reduced-motion`
 *    is checked once at module load (for synchronous first-paint decisions) and
 *    observed live, so toggling the OS setting takes effect without a reload.
 *  - Entrances are one-shot. Elements reveal once and stop being observed —
 *    re-triggering on scroll-back reads as noise.
 *  - Pointer-reactive atmosphere is throttled to one write per animation frame
 *    and disabled on coarse pointers, where "cursor position" is meaningless.
 *  - Movement is transform/opacity only, so it stays on the compositor.
 *
 * Deliberately dependency-free: the whole system is a few kB instead of
 * pulling in a layout-animation runtime the product does not need.
 */

import { useCallback, useEffect, useRef, useState } from "react";

const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";
const COARSE_POINTER_QUERY = "(hover: none), (pointer: coarse)";

/** Synchronous check — safe during render for a first-paint decision. */
export function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia(REDUCED_MOTION_QUERY).matches;
}

/** Reactive preference, kept in sync with the OS setting. */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(prefersReducedMotion);

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mql = window.matchMedia(REDUCED_MOTION_QUERY);
    const onChange = (event: MediaQueryListEvent) => setReduced(event.matches);
    mql.addEventListener("change", onChange);
    setReduced(mql.matches);
    return () => mql.removeEventListener("change", onChange);
  }, []);

  return reduced;
}

/** True on touch/coarse devices, where hover-driven effects are meaningless. */
export function useCoarsePointer(): boolean {
  const [coarse, setCoarse] = useState(() =>
    typeof window !== "undefined" && window.matchMedia
      ? window.matchMedia(COARSE_POINTER_QUERY).matches
      : false,
  );

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mql = window.matchMedia(COARSE_POINTER_QUERY);
    const onChange = (event: MediaQueryListEvent) => setCoarse(event.matches);
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);

  return coarse;
}

/**
 * Reveal-on-enter.
 *
 * Returns a ref to attach plus the current revealed state; the element is also
 * stamped with `data-revealed` so CSS owns the transition itself. When motion
 * is reduced, or IntersectionObserver is unavailable, content is revealed
 * immediately rather than being hidden behind script that may never run.
 */
export function useReveal<T extends HTMLElement = HTMLDivElement>(options?: {
  /** Fires this far before the element enters the viewport. */
  rootMargin?: string;
  threshold?: number;
  /** Keep observing and re-hide when scrolled away. */
  repeat?: boolean;
}) {
  const { rootMargin = "0px 0px -8% 0px", threshold = 0.06, repeat } =
    options ?? {};
  const ref = useRef<T | null>(null);
  const reduced = useReducedMotion();
  const [revealed, setRevealed] = useState(() => prefersReducedMotion());

  useEffect(() => {
    if (reduced) {
      setRevealed(true);
      return;
    }
    const node = ref.current;
    if (!node || typeof IntersectionObserver === "undefined") {
      setRevealed(true);
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setRevealed(true);
            if (!repeat) observer.unobserve(entry.target);
          } else if (repeat) {
            setRevealed(false);
          }
        }
      },
      { rootMargin, threshold },
    );

    observer.observe(node);
    return () => observer.disconnect();
  }, [reduced, rootMargin, threshold, repeat]);

  return { ref, revealed } as const;
}

/**
 * Pointer-reactive field.
 *
 * Publishes normalised pointer coordinates as CSS custom properties
 * (`--pointer-x` / `--pointer-y`, each in -0.5…0.5) on the referenced element,
 * letting CSS move atmosphere layers without a React re-render per mouse move.
 * At most one write per frame.
 */
export function usePointerField<T extends HTMLElement = HTMLDivElement>(
  options?: { disabled?: boolean },
) {
  const ref = useRef<T | null>(null);
  const reduced = useReducedMotion();
  const coarse = useCoarsePointer();
  const disabled = options?.disabled === true || reduced || coarse;

  useEffect(() => {
    const node = ref.current;
    if (!node || disabled) return;

    let frame = 0;
    let latest = { x: 0, y: 0 };

    const apply = () => {
      frame = 0;
      node.style.setProperty("--pointer-x", latest.x.toFixed(4));
      node.style.setProperty("--pointer-y", latest.y.toFixed(4));
    };

    const onMove = (event: PointerEvent) => {
      const rect = node.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      latest = {
        x: (event.clientX - rect.left) / rect.width - 0.5,
        y: (event.clientY - rect.top) / rect.height - 0.5,
      };
      if (!frame) frame = requestAnimationFrame(apply);
    };

    const onLeave = () => {
      latest = { x: 0, y: 0 };
      if (!frame) frame = requestAnimationFrame(apply);
    };

    node.addEventListener("pointermove", onMove, { passive: true });
    node.addEventListener("pointerleave", onLeave, { passive: true });

    return () => {
      if (frame) cancelAnimationFrame(frame);
      node.removeEventListener("pointermove", onMove);
      node.removeEventListener("pointerleave", onLeave);
    };
  }, [disabled]);

  return ref;
}

/** Document scroll offset in pixels, throttled to one update per frame. */
export function useScrollY(): number {
  const [y, setY] = useState(0);

  useEffect(() => {
    let frame = 0;
    const read = () => {
      frame = 0;
      setY(window.scrollY);
    };
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(read);
    };
    read();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      if (frame) cancelAnimationFrame(frame);
      window.removeEventListener("scroll", onScroll);
    };
  }, []);

  return y;
}

/**
 * Boolean flag that flips once the page is scrolled past `offset`. Lets the
 * navigation densify its surface without re-rendering on every scroll tick.
 */
export function useScrolled(offset = 12): boolean {
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    let frame = 0;
    const read = () => {
      frame = 0;
      setScrolled(window.scrollY > offset);
    };
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(read);
    };
    read();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      if (frame) cancelAnimationFrame(frame);
      window.removeEventListener("scroll", onScroll);
    };
  }, [offset]);

  return scrolled;
}

/**
 * Lock body scroll while an overlay is open, restoring the previous values.
 * Compensates for the scrollbar width so the page does not shift sideways
 * underneath a modal.
 */
export function useScrollLock(active: boolean): void {
  useEffect(() => {
    if (!active) return;
    const { body } = document;
    const previousOverflow = body.style.overflow;
    const previousPadding = body.style.paddingRight;
    const gap = window.innerWidth - document.documentElement.clientWidth;

    body.style.overflow = "hidden";
    if (gap > 0) body.style.paddingRight = `${gap}px`;

    return () => {
      body.style.overflow = previousOverflow;
      body.style.paddingRight = previousPadding;
    };
  }, [active]);
}

/** Run a handler for Escape, only while `enabled`. */
export function useEscape(enabled: boolean, handler: () => void): void {
  const saved = useRef(handler);
  saved.current = handler;

  useEffect(() => {
    if (!enabled) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") saved.current();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enabled]);
}

/**
 * One-shot confirmation flag, e.g. the tick after saving. Auto-clears.
 * Returns [active, trigger].
 */
export function usePulse(durationMs = 900) {
  const [active, setActive] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  const trigger = useCallback(() => {
    setActive(true);
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setActive(false), durationMs);
  }, [durationMs]);

  useEffect(
    () => () => {
      if (timer.current) window.clearTimeout(timer.current);
    },
    [],
  );

  return [active, trigger] as const;
}

/**
 * Staggered entrance delay for a list, capped so long lists never feel slow.
 * Returns a ready-to-use CSS `animation-delay` value.
 */
export function staggerDelay(index: number, step = 45, max = 8): string {
  return `${Math.min(Math.max(index, 0), max) * step}ms`;
}

/**
 * Trap focus inside an overlay and return focus to the opener on close.
 * Used by every modal, drawer and the command palette so keyboard users can
 * never tab behind an overlay.
 */
export function useFocusTrap(active: boolean) {
  const containerRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!active) return;
    const container = containerRef.current;
    if (!container) return;

    const opener = document.activeElement as HTMLElement | null;

    const selector = [
      "a[href]",
      "button:not([disabled])",
      "input:not([disabled])",
      "select:not([disabled])",
      "textarea:not([disabled])",
      "[tabindex]:not([tabindex='-1'])",
    ].join(",");

    const focusables = () =>
      Array.from(container.querySelectorAll<HTMLElement>(selector)).filter(
        (el) => el.offsetParent !== null || el === document.activeElement,
      );

    // Move focus in without stealing it from an element that already has it.
    if (!container.contains(document.activeElement)) {
      const first = focusables()[0];
      (first ?? container).focus({ preventScroll: true });
    }

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const items = focusables();
      if (items.length === 0) {
        event.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    container.addEventListener("keydown", onKeyDown);
    return () => {
      container.removeEventListener("keydown", onKeyDown);
      opener?.focus?.({ preventScroll: true });
    };
  }, [active]);

  return containerRef;
}
