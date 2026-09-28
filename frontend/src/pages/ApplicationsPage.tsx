import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../services/api";
import type { ApplicationOut, ApplicationStatus } from "../types";

const STATUS_LABELS: Record<ApplicationStatus, string> = {
  viewed: "Viewed",
  saved: "Saved",
  application_started: "Application started",
  applied: "Applied",
  rejected: "Rejected",
  interview: "Interview",
  offer: "Offer",
};

const STATUS_COLORS: Record<ApplicationStatus, string> = {
  viewed: "badge",
  saved: "badge-accent",
  application_started: "badge-warning",
  applied: "badge-success",
  rejected: "badge-danger",
  interview: "badge-accent",
  offer: "badge-success",
};

export function ApplicationsPage() {
  const [applications, setApplications] = useState<ApplicationOut[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.applications()
      .then(setApplications)
      .catch(() => setApplications([]))
      .finally(() => setLoading(false));
  }, []);

  const grouped = applications.reduce((acc, app) => {
    if (!acc[app.status]) acc[app.status] = [];
    acc[app.status].push(app);
    return acc;
  }, {} as Record<ApplicationStatus, ApplicationOut[]>);

  return (
    <main className="container" style={{ padding: "var(--sp-20) 0" }}>
      <header className="anim-fade-up">
        <h1 style={{ fontSize: "var(--text-2xl)", marginBottom: "var(--sp-4)" }}>
          My Applications
        </h1>
        <p className="muted">
          Track your application progress across opportunities.
        </p>
      </header>

      {loading ? (
        <div className="skeleton" style={{ height: 300, borderRadius: "var(--radius-lg)" }} />
      ) : applications.length === 0 ? (
        <div className="glass anim-fade-up" style={{ padding: "var(--sp-8)", marginTop: "var(--sp-8)" }}>
          <p className="muted">No applications tracked yet.</p>
          <Link to="/opportunities" className="btn btn-primary" style={{ marginTop: "var(--sp-4)" }}>
            Explore opportunities →
          </Link>
        </div>
      ) : (
        <div style={{ display: "grid", gap: "var(--sp-8)", marginTop: "var(--sp-8)" }}>
          {Object.entries(grouped).map(([status, apps]) => (
            <section key={status} className="anim-fade-up">
              <h2 style={{ marginBottom: "var(--sp-4)", fontSize: "var(--text-lg)" }}>
                {STATUS_LABELS[status as ApplicationStatus]} ({apps.length})
              </h2>
              <div style={{ display: "grid", gap: "var(--sp-3)" }}>
                {apps.map((app) => (
                  <Link
                    key={app.id}
                    to={`/opportunities/${app.job.slug}`}
                    className="glass"
                    style={{
                      padding: "var(--sp-4)",
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                    }}
                  >
                    <div>
                      <h3 style={{ fontSize: "var(--text-md)" }}>{app.job.title}</h3>
                      <p className="muted" style={{ fontSize: "var(--text-sm)" }}>
                        {app.job.company} · {app.job.location_label}
                      </p>
                    </div>
                    <span className={`badge ${STATUS_COLORS[app.status]}`}>
                      {STATUS_LABELS[app.status]}
                    </span>
                  </Link>
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </main>
  );
}
