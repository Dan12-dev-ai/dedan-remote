import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { JobCard, JobCardSkeleton } from "../components/jobs/JobCard";
import { EmptyState, ErrorState } from "../components/common/States";
import { ApiError, api } from "../services/api";
import { useAuth } from "../stores/AuthContext";
import type { SavedItem } from "../types";

/** Saved opportunities — requires authentication. */
export function SavedPage() {
  const { user } = useAuth();
  const [items, setItems] = useState<SavedItem[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    if (!user) return;
    setItems(null);
    setError(null);
    api
      .saved()
      .then(setItems)
      .catch((err: unknown) =>
        err instanceof ApiError ? err : null,
      );
  }, [user]);

  if (!user) {
    return (
      <main className="container container--standard">
        <h1>Saved opportunities</h1>
        <EmptyState
          title="Sign in to save opportunities"
          description="Saving keeps opportunities in your personal list with notes — no other personal data is stored."
          action={<Link to="/" className="btn btn-primary">Back to home</Link>}
        />
      </main>
    );
  }

  return (
    <main className="saved-page">
      {/* Page Header */}
      <section className="section section--standard">
        <header className="cluster cluster--horizontal">
          <h1>Saved opportunities</h1>
          <p className="muted">
            {items
              ? `${items.length} saved ${items.length === 1 ? "item" : "items"}`
              : "Loading…"}
          </p>
        </header>
      </section>

      {/* Page Content */}
      <section className="section section--wide">
        {error ? (
          <ErrorState message={error.message} />
        ) : !items ? (
          <div className="cluster cluster--vertical">
            {Array.from({ length: 3 }).map((_, i) => (
              <JobCardSkeleton key={i} />
            ))}
          </div>
        ) : items.length === 0 ? (
          <EmptyState
            title="No saved opportunities yet"
            description="Tap the heart on any opportunity to keep it here."
            suggestions={[
              "Browse the explorer and save roles that interest you",
              "Add notes to remember why you saved something",
              "Saved items sync with your application tracker",
            ]}
            action={<Link to="/opportunities" className="btn btn-primary">Explore opportunities</Link>}
          />
        ) : (
          <div className="cluster cluster--vertical">
            {items.map((item, i) => (
              <div key={item.job.id}>
                <JobCard
                  job={item.job}
                  index={i}
                  authenticated
                  saved
                  onSaveToggle={(job, next) => {
                    if (!next) {
                      api.unsaveJob(job.id).then(() => {
                        setItems((prev) =>
                          (prev ?? []).filter(
                            (x) => x.job.id !== job.id,
                          ),
                        );
                      }).catch(() => undefined);
                    }
                  }}
                />
                {item.note && (
                  <p className="muted">
                    Note: {item.note}
                  </p>
                )}
              </div>
            ))}
          </div>
        )}
      </section>
    </main>
  );
}
