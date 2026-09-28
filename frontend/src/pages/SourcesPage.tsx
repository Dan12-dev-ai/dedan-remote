import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../services/api";
import type { SourceStat } from "../types";

export function SourcesPage() {
  const [sources, setSources] = useState<SourceStat[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.sources()
      .then(setSources)
      .catch(() => setSources([]))
      .finally(() => setLoading(false));
  }, []);

  return (
    <main className="container" style={{ padding: "var(--sp-20) 0" }}>
      <header className="anim-fade-up">
        <h1 style={{ fontSize: "var(--text-2xl)", marginBottom: "var(--sp-4)" }}>
          Monitored Sources
        </h1>
        <p className="muted">
          Platforms the discovery engine watches. Listings are linked to their original source.
        </p>
      </header>

      {loading ? (
        <div className="skeleton" style={{ height: 200, borderRadius: "var(--radius-lg)" }} />
      ) : sources.length === 0 ? (
        <p className="muted">No sources registered yet.</p>
      ) : (
        <div style={{ display: "grid", gap: "var(--sp-4)", marginTop: "var(--sp-8)" }}>
          {sources.map((src) => (
            <Link
              key={src.id}
              to={`/opportunities?source=${src.id}`}
              className="glass anim-fade-up"
              style={{
                padding: "var(--sp-5)",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <div>
                <h3 style={{ fontSize: "var(--text-md)" }}>{src.name}</h3>
                <p className="muted" style={{ fontSize: "var(--text-sm)" }}>
                  {src.job_count} opportunities discovered
                </p>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: "var(--sp-3)" }}>
                <span
                  className={`badge ${src.circuit_open ? "badge-danger" : "badge-success"}`}
                >
                  {src.circuit_open ? "Paused" : "Active"}
                </span>
                <span className="tertiary">→</span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </main>
  );
}
