/**
 * SignInButton — the primary authentication action.
 *
 * The earlier version tracked its own `isLoading` flag and awaited whatever
 * `onClick` returned. That is wrong when `onClick` opens a modal: the promise
 * resolves immediately, the spinner flashes for one frame, and the button
 * re-enables while the modal is still open. Loading state is the caller's
 * concern (it alone knows when the operation finishes), so it is a prop here.
 */
import type { ReactNode } from "react";

interface SignInButtonProps {
  onClick: () => void;
  children: ReactNode;
  /** "primary" for sign in, "ghost" for sign out inside the account menu. */
  variant?: "primary" | "ghost";
  /** Renders a non-interactive busy state without shifting the layout. */
  busy?: boolean;
  className?: string;
}

export function SignInButton({
  onClick,
  children,
  variant = "primary",
  busy = false,
  className = "",
}: SignInButtonProps) {
  const classes = [
    "btn",
    variant === "primary" ? "btn--primary" : "btn--ghost",
    variant === "ghost" ? "btn--sm" : "",
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <button
      type="button"
      className={classes}
      onClick={onClick}
      disabled={busy}
      data-busy={busy ? "true" : undefined}
      aria-busy={busy || undefined}
    >
      {children}
    </button>
  );
}
