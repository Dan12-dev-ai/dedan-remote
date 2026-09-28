import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { JobCard, JobCardSkeleton } from "../components/jobs/JobCard";
import { useAuth } from "../stores/AuthContext";
import { api } from "../services/api";
import type { RecommendationPage } from "../types";

export function RecommendationsPage() {
  const { user } = useAuth();
  const [data, setData] = useState<RecommendationPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [savedIds, setSavedIds] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!user) return;
    api.recommendations(12)
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [user]);

  useEffect(() => {
    if (!user) return;
    api.saved()
      .then((items) => setSavedIds(new Set(items.map((i) => i.job.id))))
      .catch(() => setSavedIds(new Set()));
  }, [user]);

  const handleSaveToggle = (jobId: string, next: boolean) => {
    setSavedIds((prev) => {
      const copy = new Set(prev);
      if (next) copy.add(jobId);
      else copy.delete(jobId);
      return copy;
    });
    const action = next ? api.saveJob(jobId) : api.unsaveJob(jobId);
    action.catch(() => {
      setSavedIds((prev) => {
        const copy = new Set(prev);
        if (next) copy.delete(jobId);
        else copy.add(jobId);
        return copy;
      });
    });
  };

  return (
    <main className="container" style={{ padding: "var(--sp-20) 0" }}>
      <header className="anim-fade-up">
        <h1 style={{ fontSize: "var(--text-2xl)", marginBottom: "var(--sp-4)" }}>
          Recommended for You
        </h1>
        <p className="muted">
          {data?.method || "Opportunities ranked against your preferences."}
        </p>
      </header>

      {loading ? (
        <div style={{ display: "grid", gap: "var(--sp-4)", marginTop: "var(--sp-8)" }}>
          {Array.from({ length: 6 }).map((_, i) => (
            <JobCardSkeleton key={i} />
          ))}
        </div>
      ) : data && data.items.length > 0 ? (
        <>
          <div style={{ display: "grid", gap: "var(--sp-4)", marginTop: "var(--sp-8)" }}>
            {data.items.map((job, i) => (
              <JobCard
                key={job.id}
                job={job}
                index={i}
                authenticated={!!user}
                saved={savedIds.has(job.id)}
                onSaveToggle={(job, next) => handleSaveToggle(job.id, next)}
              />
            ))}
          </div>
          <Link to="/profile" className="btn btn-secondary" style={{ marginTop: "var(--sp-8)" }}>
            Adjust preferences →
          </Link>
        </>
      ) : (
        <div className="glass anim-fade-up" style={{ padding: "var(--sp-8)", marginTop: "var(--sp-8)" }}>
          <p className="muted">
            Set your preferences to receive personalized recommendations.
          </p>
          <Link to="/profile" className="btn btn-primary" style={{ marginTop: "var(--sp-4)" }}>
            Set preferences →
          </Link>
        </div>
      )}
    </main>
  );
}
