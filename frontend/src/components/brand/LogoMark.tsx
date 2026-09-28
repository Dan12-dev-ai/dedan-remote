/**
 * LogoMark — the "D" orbital mark on its own.
 *
 * Uses the colour-keyed raster cut from the master artwork. The source crop is
 * 200px wide and the mark is never displayed taller than about 64px, so it
 * always renders as a 3x-or-better asset and stays sharp on any display.
 */
export interface LogoMarkProps {
  /** Rendered height in px. Width follows the artwork's 200:128 aspect. */
  height?: number;
  /** Empty alt by default — the mark is decorative wherever it appears. */
  alt?: string;
  className?: string;
}

const MARK_ASPECT = 200 / 128;

export function LogoMark({ height = 32, alt = "", className = "" }: LogoMarkProps) {
  return (
    <img
      className={`logo-mark${className ? ` ${className}` : ""}`}
      src="/brand/dedan-remote-mark.png"
      alt={alt}
      width={Math.round(height * MARK_ASPECT)}
      height={height}
      decoding="async"
      draggable={false}
    />
  );
}
