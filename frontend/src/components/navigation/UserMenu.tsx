/**
 * UserMenu — the account dropdown.
 *
 * `trigger` renders *inside* the button, so it must be non-interactive (an
 * avatar or an initial). Menu entries go in `children` and live outside the
 * button, which keeps the markup free of nested interactive elements — the
 * previous version wrapped a `<Link>` and a `<button>` inside a `<button>`,
 * which is invalid HTML and unusable by keyboard.
 */
import { useEffect, useRef, useState, type ReactNode } from "react";

interface UserMenuProps {
  /** Non-interactive content for the trigger (avatar, initial). */
  trigger: ReactNode;
  /** Menu entries — links and buttons. */
  children?: ReactNode;
  /** Accessible name for the trigger. */
  label?: string;
}

export function UserMenu({
  trigger,
  children,
  label = "Account menu",
}: UserMenuProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  // Dismiss on outside pointer and on Escape — both are expected of a
  // disclosure menu and cost nothing to support.
  useEffect(() => {
    if (!open) return;

    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };

    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div className="user-menu" ref={rootRef}>
      <button
        type="button"
        className="nav-action user-menu__trigger"
        onClick={() => setOpen((v) => !v)}
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        {trigger}
      </button>

      {open && (
        <div className="user-menu__dropdown">
          {/* Closing on activation keeps the menu from lingering over the
              destination the user just navigated to. */}
          <div className="user-menu__items" onClick={() => setOpen(false)}>
            {children}
          </div>
        </div>
      )}
    </div>
  );
}
