import type { ReactNode } from "react";
import "./States.css";

interface EmptyStateProps {
  title: string;
  description?: string;
  suggestions?: string[];
  action?: ReactNode;
  icon?: ReactNode;
}

/** Premium empty state — guides without pretending data exists. */
export function EmptyState({
  title,
  description,
  suggestions,
  action,
  icon,
}: EmptyStateProps) {
  return (
    <div className="state" role="status">
      <div className="state__icon" aria-hidden="true">
        {icon ?? "◌"}
      </div>
      <h3 className="state__title">{title}</h3>
      {description && <p className="state__desc">{description}</p>}
      {suggestions && suggestions.length > 0 && (
        <ul className="state__list">
          {suggestions.map((s) => (
            <li key={s}>{s}</li>
          ))}
        </ul>
      )}
      {action && <div className="state__action">{action}</div>}
    </div>
  );
}

interface ErrorStateProps {
  title?: string;
  message?: string;
  onRetry?: () => void;
}

/** Professional error UI — no raw exceptions, offers recovery. */
export function ErrorState({
  title = "We couldn't load opportunities.",
  message = "The discovery service may be temporarily unavailable.",
  onRetry,
}: ErrorStateProps) {
  return (
    <div className="state state--error" role="alert">
      <div className="state__icon state__icon--error" aria-hidden="true">
        !
      </div>
      <h3 className="state__title">{title}</h3>
      <p className="state__desc">{message}</p>
      {onRetry && (
        <div className="state__action">
          <button type="button" className="btn btn-secondary"
            onClick={onRetry}>
            Retry
          </button>
        </div>
      )}
    </div>
  );
}
