/**
 * Navigation icons.
 *
 * Inline SVG rather than an icon font or sprite sheet: these are the only icons
 * in the product, there are six of them, and inlining keeps them colour- and
 * stroke-controllable from CSS with no extra network request.
 *
 * Every icon is `aria-hidden` and `focusable="false"` — the accessible name
 * always comes from the control that wraps it, never from the glyph.
 */

const base = {
  viewBox: "0 0 20 20",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.6,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  width: 18,
  height: 18,
  "aria-hidden": true,
  focusable: "false" as const,
};

export function SearchIcon() {
  return (
    <svg {...base}>
      <circle cx="9" cy="9" r="5.5" />
      <path d="m13.2 13.2 3.3 3.3" />
    </svg>
  );
}

export function BellIcon() {
  return (
    <svg {...base}>
      <path d="M6 8a4 4 0 1 1 8 0c0 3 1.2 4.4 1.2 4.4H4.8S6 11 6 8Z" />
      <path d="M8.4 15.2a1.8 1.8 0 0 0 3.2 0" />
    </svg>
  );
}

export function MenuIcon() {
  return (
    <svg {...base}>
      <path d="M3.5 6h13M3.5 10h13M3.5 14h13" />
    </svg>
  );
}

export function CloseIcon() {
  return (
    <svg {...base}>
      <path d="m5.5 5.5 9 9M14.5 5.5l-9 9" />
    </svg>
  );
}

export function SparkIcon() {
  return (
    <svg {...base}>
      <path d="M10 2.8 11.7 8l5.2 1.7L11.7 11.4 10 16.6 8.3 11.4 3.1 9.7 8.3 8Z" />
    </svg>
  );
}

export function ArrowLeftIcon() {
  return (
    <svg {...base}>
      <path d="M15.5 10h-11M9 4.5 3.5 10 9 15.5" />
    </svg>
  );
}
