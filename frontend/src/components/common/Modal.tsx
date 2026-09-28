import { useEffect, useRef, type ReactNode } from "react";
import "./Modal.css";

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: ReactNode;
  /** Accessible label when title is omitted */
  ariaLabel?: string;
  maxWidth?: number;
}

/** Accessible dialog: focus trap, Escape to close, backdrop dismiss. */
export function Modal({
  open,
  onClose,
  title,
  children,
  ariaLabel,
  maxWidth = 460,
}: ModalProps) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const previouslyFocused = useRef<Element | null>(null);

  useEffect(() => {
    if (!open) return;
    previouslyFocused.current = document.activeElement;
    const panel = panelRef.current;
    const focusable = panel?.querySelectorAll<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    );
    focusable?.[0]?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== "Tab" || !focusable || focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey, true);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    return () => {
      document.removeEventListener("keydown", onKey, true);
      document.body.style.overflow = prevOverflow;
      (previouslyFocused.current as HTMLElement | null)?.focus?.();
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="modal__backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={panelRef}
        className="modal__panel"
        style={{ maxWidth }}
        role="dialog"
        aria-modal="true"
        aria-label={title ?? ariaLabel ?? "Dialog"}
      >
        {title && (
          <header className="modal__header">
            <h2 className="modal__title">{title}</h2>
            <button
              type="button"
              className="modal__close"
              onClick={onClose}
              aria-label="Close dialog"
            >
              ✕
            </button>
          </header>
        )}
        <div className="modal__body">{children}</div>
      </div>
    </div>
  );
}
