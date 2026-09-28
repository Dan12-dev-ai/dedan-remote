import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../../services/api";
import { useEscape } from "../../lib/motion";
import { relativeTime, absoluteDateTime } from "../../lib/format";
import type { NotificationOut } from "../../types";
import "./ActivityFeed.css";

/**
 * Activity feed popover — recent saved/application/discovery events from the
 * authenticated user's `/api/notifications`.
 *
 * Timestamps come straight from the API's `at` field; relative wording is
 * derived from the real instant (never invented "just now" filler), with the
 * absolute date available in the row's `title`.
 */
export function ActivityFeed() {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<NotificationOut[] | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const reqId = useRef(0);

  const load = useCallback(() => {
    const id = ++reqId.current;
    setLoading(true);
    setError(null);
    api
      .notifications(15)
      .then((res) => {
        if (reqId.current !== id) return;
        setItems(res.items);
        setNote(res.note || null);
      })
      .catch((err: unknown) => {
        if (reqId.current !== id) return;
        setError(
          err instanceof ApiError
            ? err.message
            : "Activity is unavailable right now.",
        );
      })
      .finally(() => {
        if (reqId.current === id) setLoading(false);
      });
  }, []);

  // Refresh each time the popover opens so timestamps stay current.
  useEffect(() => {
    if (open) load();
  }, [open, load]);

  useEscape(open, () => setOpen(false));

  // Click-outside dismiss.
  useEffect(() => {
    if (!open) return;
    const onPointer = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onPointer);
    return () => document.removeEventListener("mousedown", onPointer);
  }, [open]);

  const kindIcon: Record<NotificationOut["kind"], string> = {
    saved: "★",
    application: "✓",
    discovery: "◆",
  };

  return (
    <div className="feed" ref={rootRef}>
      <button
        type="button"
        className="feed__trigger"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label="Activity feed"
        onClick={() => setOpen((v) => !v)}
      >
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none"
          aria-hidden="true">
          <path
            d="M8 1.75c-2.35 0-4.25 1.9-4.25 4.25v2.4L2.5 10.5h11l-1.25-2.05V6c0-2.35-1.9-4.25-4.25-4.25Z"
            stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
          <path d="M6.4 12.4a1.7 1.7 0 0 0 3.2 0"
            stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        </svg>
      </button>

      {open && (
        <div
          className="feed__panel"
          role="dialog"
          aria-label="Activity feed"
        >
          <header className="feed__head">
            <span>Activity</span>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => setOpen(false)}
            >
              Close
            </button>
          </header>

          <div className="feed__body" aria-live="polite">
            {loading && items === null && (
              <div className="feed__skeletons">
                {[0, 1, 2].map((i) => (
                  <div
                    key={i}
                    className="skeleton"
                    style={{ height: 44, borderRadius: 10 }}
                  />
                ))}
              </div>
            )}

            {error && (
              <div className="feed__msg" role="alert">
                <p>{error}</p>
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={load}
                >
                  Retry
                </button>
              </div>
            )}

            {!loading && !error && items !== null && items.length === 0 && (
              <div className="feed__msg">
                <p>No activity yet.</p>
                <p className="tertiary">
                  Saving an opportunity or tracking an application shows up
                  here.
                </p>
              </div>
            )}

            {items !== null && items.length > 0 && (
              <ul className="feed__list">
                {items.map((item) => {
                  const rel = relativeTime(item.at);
                  const abs = absoluteDateTime(item.at);
                  return (
                    <li key={item.id} className="feed__item">
                      <span
                        className={`feed__kind feed__kind--${item.kind}`}
                        aria-hidden="true"
                      >
                        {kindIcon[item.kind]}
                      </span>
                      <div className="feed__content">
                        <p className="feed__title">{item.title}</p>
                        {item.detail && (
                          <p className="feed__detail">{item.detail}</p>
                        )}
                        <div className="feed__meta">
                          <time dateTime={item.at} title={abs ?? undefined}>
                            {rel ?? abs ?? "Timestamp unavailable"}
                          </time>
                          {item.job && (
                            <Link
                              to={`/opportunities/${item.job.slug}`}
                              className="feed__link"
                              onClick={() => setOpen(false)}
                            >
                              View opportunity →
                            </Link>
                          )}
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          {note && <footer className="feed__note">{note}</footer>}
        </div>
      )}
    </div>
  );
}
