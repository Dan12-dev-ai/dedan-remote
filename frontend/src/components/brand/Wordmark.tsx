/**
 * Wordmark — "DEDAN REMOTE" as live text.
 *
 * The master logo's wordmark cannot survive being scaled into app chrome: at
 * navbar height the REMOTE sub-word would shrink to roughly 1.6px and turn to
 * mush. Rendering it as text instead keeps both lines crisp at every size and
 * lets the mark stay a bitmap, so chrome never depends on a raster table.
 *
 * The gradient "A" and the flanking rules are the two devices that make the
 * lockup recognisable, so they are reproduced here.
 */
export interface WordmarkProps {
  /** Renders the "REMOTE" sub-word beneath the primary word. */
  showSub?: boolean;
  /** Accessible name; pass `null` when an ancestor already names the link. */
  label?: string | null;
  className?: string;
}

export function Wordmark({ showSub = true, label = "DEDAN Remote", className = "" }: WordmarkProps) {
  return (
    <span
      className={`wordmark${className ? ` ${className}` : ""}`}
      {...(label ? { role: "img", "aria-label": label } : { "aria-hidden": "true" })}
    >
      <span className="wordmark__primary">
        DED<span className="wordmark__accent">A</span>N
      </span>
      {showSub && <span className="wordmark__sub">REMOTE</span>}
    </span>
  );
}
