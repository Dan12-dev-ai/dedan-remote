/**
 * NavAction — the one icon-only control in the navigation.
 *
 * Search, notifications, the mobile menu and the account menu are all the same
 * object: a square, icon-only button that may carry a count badge. Declaring it
 * once means the four of them cannot drift in size, hit area or focus
 * treatment, which is exactly what had happened when each had its own class and
 * only one of them was styled at all.
 *
 * `label` is required and has no default: an icon-only control with no
 * accessible name is unusable with a screen reader, so the type system refuses
 * to let a caller omit it.
 */
import type { ReactNode } from "react";

export interface NavActionProps {
  /** Accessible name. Required — these controls have no visible text. */
  label: string;
  children: ReactNode;
  onClick?: () => void;
  /** Count shown in the corner badge; values over 99 render as "99+". */
  count?: number;
  /** Marks the control as controlling a popup, e.g. a menu or dialog. */
  hasPopup?: "menu" | "dialog" | "true";
  expanded?: boolean;
  /** ID of the element this control owns. */
  controls?: string;
  className?: string;
}

export function NavAction({
  label,
  children,
  onClick,
  count,
  hasPopup,
  expanded,
  controls,
  className = "",
}: NavActionProps) {
  return (
    <button
      type="button"
      className={`nav-action${className ? ` ${className}` : ""}`}
      onClick={onClick}
      aria-label={label}
      title={label}
      {...(hasPopup ? { "aria-haspopup": hasPopup } : {})}
      {...(expanded !== undefined ? { "aria-expanded": expanded } : {})}
      {...(controls ? { "aria-controls": controls } : {})}
      {...(count ? { "data-badged": "true" } : {})}
    >
      {children}
      {count !== undefined && count > 0 && (
        <span className="nav-action__badge" aria-hidden="true">
          {count > 99 ? "99+" : count}
        </span>
      )}
    </button>
  );
}
