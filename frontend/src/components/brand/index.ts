/**
 * DEDAN Remote — Brand component set (Set 03).
 *
 * The artwork is derived from the master logo by
 * `scripts/build_brand_assets.py`, which colour-keys the master's navy plate
 * into real alpha. Every variant therefore composites onto any surface without
 * a visible box, which is why the raster lockups are used rather than a
 * hand-redrawn SVG: the mark's inner glow cannot be reproduced faithfully by
 * hand.
 */
export { Brand, BRAND_SOURCES } from "./Brand";
export type { BrandProps, BrandSize, BrandVariant } from "./Brand";
export { LogoMark } from "./LogoMark";
export { Wordmark } from "./Wordmark";
export type { WordmarkProps } from "./Wordmark";
