/**
 * Brand — the single entry point for DEDAN Remote lockups.
 *
 * Five variants, each chosen for a different legibility budget:
 *
 *  composite      raster mark + live-text wordmark. Default; the only variant
 *                 that stays crisp inside app chrome at every size.
 *  lockup         raster mark + DEDAN REMOTE. Use at >= 36px tall.
 *  lockup-tagline raster lockup + tagline. `hero` size only — see HEIGHTS.
 *  mark           the D alone. Favicons, compact rails, dense headers.
 *  wordmark       text only, no artwork.
 *
 * Accessibility: exactly one element carries the accessible name. Every piece
 * of artwork is decorative (`alt=""`), so a screen reader hears "DEDAN Remote"
 * once rather than the alt text plus the visible wordmark text.
 */
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { LogoMark } from "./LogoMark";
import { Wordmark } from "./Wordmark";
import "./Brand.css";

/** Colour-keyed cuts of the master artwork (see scripts/build_brand_assets.py). */
export const BRAND_SOURCES = {
  mark: "/brand/dedan-remote-mark.png",
  lockup: "/brand/dedan-remote-lockup-tight.png",
  lockupTagline: "/brand/dedan-remote-lockup.png",
} as const;

export type BrandVariant =
  | "composite"
  | "lockup"
  | "lockup-tagline"
  | "mark"
  | "wordmark";

export type BrandSize = "xs" | "sm" | "md" | "lg" | "xl" | "hero";

/**
 * Rendered artwork height in px for each size step.
 *
 * `hero` exists because the tagline inside `lockup-tagline` occupies only 6% of
 * the artwork's height: to render that tagline at a legible 11px the artwork
 * itself must be ~176px tall. Offering `lockup-tagline` at navbar sizes would
 * therefore produce a smudge, so it is restricted to this step.
 */
const HEIGHTS: Record<BrandSize, number> = {
  xs: 20,
  sm: 26,
  md: 32,
  lg: 44,
  xl: 60,
  hero: 176,
};

const ASPECT = {
  mark: 200 / 128,
  lockup: 640 / 165,
  lockupTagline: 640 / 209,
} as const;

export interface BrandProps {
  variant?: BrandVariant;
  size?: BrandSize;
  /** When set, the lockup becomes a client-side link to this route. */
  to?: string;
  onClick?: () => void;
  /**
   * Accessible name. Defaults to "DEDAN Remote, home". Pass `null` when an
   * ancestor already provides the name (e.g. the lockup sits inside a labelled
   * navigation link) so the name is not announced twice.
   */
  label?: string | null;
  /** Force the "REMOTE" sub-word on or off. Defaults to on for lg/xl. */
  showSub?: boolean;
  className?: string;
}

export function Brand({
  variant = "composite",
  size = "md",
  to,
  onClick,
  label = "DEDAN Remote, home",
  showSub,
  className = "",
}: BrandProps) {
  const height = HEIGHTS[size];
  const withSub = showSub ?? (size === "lg" || size === "xl");

  let art: ReactNode;
  switch (variant) {
    case "mark":
      art = <LogoMark height={height} />;
      break;
    case "wordmark":
      art = <Wordmark showSub={withSub} label={null} />;
      break;
    case "lockup":
    case "lockup-tagline": {
      const src = variant === "lockup" ? BRAND_SOURCES.lockup : BRAND_SOURCES.lockupTagline;
      const aspect = variant === "lockup" ? ASPECT.lockup : ASPECT.lockupTagline;
      art = (
        <img
          className="brand__art"
          src={src}
          alt=""
          width={Math.round(height * aspect)}
          height={height}
          decoding="async"
          draggable={false}
        />
      );
      break;
    }
    case "composite":
    default:
      art = (
        <>
          <LogoMark height={height} />
          <Wordmark showSub={withSub} label={null} />
        </>
      );
  }

  const classes = [
    "brand",
    `brand--${variant}`,
    `brand--${size}`,
    to ? "brand--link" : "",
    className,
  ]
    .filter(Boolean)
    .join(" ");

  // The accessible name lives here and nowhere else.
  const semantics = label
    ? { role: "img" as const, "aria-label": label }
    : { "aria-hidden": true as const };

  if (to) {
    return (
      <Link
        to={to}
        onClick={onClick}
        className={classes}
        aria-label={label ?? undefined}
        style={{ ["--brand-h" as string]: `${height}px` }}
      >
        {art}
      </Link>
    );
  }

  return (
    <span className={classes} {...semantics} style={{ ["--brand-h" as string]: `${height}px` }}>
      {art}
    </span>
  );
}
