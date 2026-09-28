import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, api } from "../services/api";
import { useAuth } from "../stores/AuthContext";
import { ApplyModal } from "../components/jobs/ApplyModal";
import { SaveButton } from "../components/jobs/SaveButton";
import { ErrorState } from "../components/common/States";
import type { JobDetail } from "../types";

function formatUtc(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString("en-GB", {
      day: "numeric", month: "short", year: "numeric",
      hour: "2-digit", minute: "2-digit", timeZone: "UTC",
      timeZoneName: "short",
    });
  } catch {
    return iso;
  }
}

/** Full-screen opportunity page with distributed composition. */
export function JobDetailPage() {
  const { slug = "" } = useParams();
  const { user } = useAuth();
  const [job, setJob] = useState<JobDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [saved, setSaved] = useState(false);
  const [applyOpen, setApplyOpen] = useState(false);
  const [trackingNote, setTrackingNote] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    setError(null);
    api
      .job(slug)
      .then(setJob)
      .catch((err: unknown) =>
        err instanceof ApiError
          ? err
          : new ApiError(0, "unknown", "Something went wrong."),
      )
      .finally(() => setLoading(false));
  };

  useEffect(load, [slug]);

  useEffect(() => {
    if (!user || !job) return;
    api
      .saved()
      .then((items) => {
        setSaved(items.some((i) => i.job.id === job.id));
      })
      .catch(() => undefined);
  }, [user, job?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // SEO: dynamic title + canonical + JobPosting structured data.
  useEffect(() => {
    if (!job) return;
    document.title = `${job.title} — ${job.company} | DEDAN Remote`;

    const setMeta = (name: string, content: string, prop = false) => {
      const selector = prop
        ? `meta[property="${name}"]`
        : `meta[name="${name}"]`;
      let el = document.head.querySelector(selector) as HTMLMetaElement | null;
      if (!el) {
        el = document.createElement("meta");
        el.setAttribute(prop ? "property" : "name", name);
        document.head.appendChild(el);
      }
      el.content = content;
    };
    setMeta(
      "description",
      `${job.title} at ${job.company} — ${job.location_label}. ` +
        "Discovered by DEDAN Remote.",
    );
    setMeta("og:title", `${job.title} — DEDAN Remote`, true);

    const ld = document.createElement("script");
    ld.type = "application/ld+json";
    ld.id = "dedan-ld-job";
    ld.textContent = JSON.stringify({
      "@context": "https://schema.org",
      "@type": "JobPosting",
      title: job.title,
      description: job.description ?? `${job.title} at ${job.company}`,
      datePosted: job.posted_date ?? undefined,
      employmentType: "CONTRACT",
      hiringOrganization: {
        "@type": "Organization",
        name: job.company,
      },
      jobLocationType: "TELECOMMUTE",
      url: job.apply_url ?? job.url ?? undefined,
    });
    document.getElementById("dedan-ld-job")?.remove();
    document.head.appendChild(ld);
  }, [job]);

  // Confirms the hand-off to the official source. The application is recorded
  // server-side so it appears in My Applications — DEDAN Remote never submits
  // on the user's behalf, but it does track that the hand-off happened.
  const onApplyConfirmed = () => {
    setApplyOpen(false);
    const jobId = job?.id;
    if (!user || !jobId) return;
    api
      .createApplication(jobId)
      .then(() => setTrackingNote("Tracked in My Applications"))
      .catch(() => setTrackingNote(null));
  };

  if (error) {
    return (
      <main className="container container--standard">
        <ErrorState
          message="Failed to load opportunity"
          onRetry={load}
        />
      </main>
    );
  }

  if (!job) {
    return (
      <main className="container container--standard">
        <div className="detail skeleton" style={{ height: 20, width: "60%" }} />
        <div className="detail skeleton" style={{ height: 16, width: "40%", marginTop: 12 }} />
        <div className="detail skeleton" style={{ height: 90, width: "100%", marginTop: 20 }} />
        <div className="detail skeleton" style={{ height: 24, width: "35%" }} />
        <div className="detail skeleton" style={{ height: 70, width: "100%", marginTop: 14 }} />
        <div className="detail skeleton" style={{ height: 70 }} />
        <div className="detail skeleton" style={{ height: 70, marginTop: 12 }} />
        <div className="detail skeleton" style={{ height: 48, marginTop: 16 }} />
      </main>
    );
  }

  return (
    <main className="job-detail page-shell">
      {/* Page Background - subtle environmental enhancement */}
      <div className="full-bleed full-bleed--height-auto" aria-hidden="true">
        {/* Environmental visual layer could go here */}
      </div>
      
      {/* Main Content - Distributed Layout */}
      <section className="section">
        {/* Page Header */}
        <header className="cluster cluster--horizontal">
          <ButtonLink to="/opportunities" className="detail__back">
            ← Back to opportunities
          </ButtonLink>
          <h1 className="job-detail__title">{job.title}</h1>
        </header>
        
        {/* Distributed Two-Column Layout */}
        <div className="split-layout split-layout--standard">
          {/* LEFT: Main Job Information */}
          <div className="split-layout__content">
            <div className="cluster cluster--vertical">
              
              {/* Job Header */}
              <section className="job-detail__header glass">
                <div className="job-detail__meta">
                  <span className="job-detail__company">{job.company}</span>
                  <span className="job-detail__dot" aria-hidden="true">·</span>
                  <span className="badge badge-accent" title="Monitored source">
                    {job.source_info.name}
                  </span>
                  <span className="job-detail__dot" aria-hidden="true">·</span>
                  <span className="job-detail__location">{job.location_label}</span>
                  {job.salary && job.salary_disclosed && (
                    <>
                      <span className="job-detail__dot" aria-hidden="true">·</span>
                      <span className="job-detail__salary">{job.salary}</span>
                    </>
                  )}
                  {job.experience_hint && (
                    <>
                      <span className="job-detail__dot" aria-hidden="true">·</span>
                      <span className="job-detail__location">{job.experience_hint}</span>
                    </>
                  )}
                </div>
                
                <div className="job-detail__actions">
                  <SaveButton
                    saved={saved}
                    onClick={() => setSaved(!saved)}
                    title={job.title}
                  />
                  <button
                    type="button"
                    className="btn btn-primary"
                    onClick={() => setApplyOpen(true)}
                  >
                    Apply
                  </button>
                </div>
              </section>
              
              {/* Job Description */}
              {job.description && (
                <section className="job-detail__section glass">
                  <h2 className="job-detail__h2">Job description</h2>
                  <div className="job-detail__content" dangerouslySetInnerHTML={{ __html: job.description }} />
                </section>
              )}
              
              {/* Classification — the fields the engine actually emits. An
                  earlier version rendered `requirements` and `benefits` arrays
                  that no schema has ever provided, so those two sections could
                  never appear on any listing. */}
              {(job.tags.length > 0 || job.category || job.experience_hint || job.remote) && (
                <section className="job-detail__section glass">
                  <h2 className="job-detail__h2">Classification</h2>
                  <div className="job-detail__content">
                    {job.experience_hint && (
                      <p>
                        Experience level: <strong>{job.experience_hint}</strong>
                      </p>
                    )}
                    {job.category && (
                      <p>
                        Category: <strong>{job.category}</strong>
                      </p>
                    )}
                    <p>
                      Remote eligibility:{" "}
                      <strong>{job.remote ? "Remote" : "Not stated as remote"}</strong>
                      {job.country ? ` · ${job.country}` : " · location not restricted"}
                    </p>
                    {job.tags.length > 0 && (
                      <div className="cluster job-detail__tags">
                        {job.tags.map((tag) => (
                          <Link
                            key={tag}
                            to={`/opportunities?tag=${encodeURIComponent(tag)}`}
                            className="badge"
                          >
                            {tag}
                          </Link>
                        ))}
                      </div>
                    )}
                  </div>
                </section>
              )}

              {/* Intelligence — the engine's own note about this listing, shown
                  verbatim so nothing is presented as a claim it did not make. */}
              {job.intelligence_note && (
                <section className="job-detail__section glass">
                  <h2 className="job-detail__h2">Intelligence signals</h2>
                  <div className="job-detail__content">
                    <p>{job.intelligence_note}</p>
                  </div>
                </section>
              )}
              
              {/* Application Process */}
              <section className="job-detail__section glass">
                <h2 className="job-detail__h2">Application process</h2>
                <ol className="job-detail__steps">
                  <li>Review the official listing on {job.source_info.name}.</li>
                  <li>Confirm eligibility and requirements on the source page.</li>
                  <li>Submit your application on the official source.</li>
                  <li>Track its status in My Applications.</li>
                </ol>
                <p className="muted job-detail__disclaimer">
                  DEDAN Remote is a discovery platform — applications are always
                  submitted on the original source.
                </p>
              </section>
            </div>
          </div>
          
          {/* RIGHT: Sticky Application Panel */}
          <div className="split-layout__sidebar">
            <aside className="sticky-panel sticky-panel--top">
              <div className="job-detail__panel glass">
                {/* Match Score Metric */}
                <div className="job-detail__metric">
                  <span className="job-detail__metric-label">System ranking</span>
                  <div className="job-detail__metric-value">
                    <span className="job-detail__score-num">{Math.round(job.score)}</span>
                    <span className="job-detail__score-den">/100</span>
                  </div>
                  <span className="job-detail__metric-basis">basis: discovery engine weights</span>
                </div>
                
                {/* Match Explanation */}
                {job.score_explanation && (
                  <div className="job-detail__metric">
                    <span className="job-detail__metric-label">Match explanation</span>
                    <p className="job-detail__metric-value">
                      {job.score_explanation.label}
                    </p>
                  </div>
                )}
                
                {/* Skills Match */}
                {job.preference_match && (
                  <div className="job-detail__metric">
                    <span className="job-detail__metric-label">Preference match</span>
                    <div className="job-detail__metric-value">
                      <span className="job-detail__score-num">{Math.round(job.preference_match.score)}</span>
                      <span className="job-detail__score-den">/100</span>
                    </div>
                  </div>
                )}
                
                {/* Application Tracking */}
                {trackingNote && (
                  <div className="job-detail__metric">
                    <span className="job-detail__metric-label">Application note</span>
                    <p className="job-detail__metric-value">{trackingNote}</p>
                  </div>
                )}
                
                {/* Source Information */}
                <div className="job-detail__metric">
                  <span className="job-detail__metric-label">Source</span>
                  <p className="job-detail__metric-value">
                    {job.source_info.name}
                    {/* An external hop must be a real anchor — React Router's
                        <Link> would try to resolve it against the router and
                        silently navigate in-app. */}
                    {job.source_info.homepage && (
                      <a
                        href={job.source_info.homepage}
                        target="_blank"
                        rel="noopener noreferrer nofollow"
                        className="job-detail__source-link"
                        aria-label={`Open ${job.source_info.name} homepage`}
                      >
                        ↗
                      </a>
                    )}
                  </p>
                </div>
                
                {/* Posted Date */}
                <div className="job-detail__metric">
                  <span className="job-detail__metric-label">Posted</span>
                  <p className="job-detail__metric-value">
                    {formatUtc(job.posted_date ?? "")}
                  </p>
                </div>
                
                {/* Discovery Info */}
                <div className="job-detail__metric">
                  <span className="job-detail__metric-label">Discovered</span>
                  <p className="job-detail__metric-value">
                    {job.discovered_at ? formatUtc(job.discovered_at) : "Just now"}
                  </p>
                </div>
              </div>
            </aside>
          </div>
        </div>
      </section>
      
      {/* Apply Modal - Floating Panel */}
      <ApplyModal
        open={applyOpen}
        job={job}
        onCancel={() => setApplyOpen(false)}
        onConfirm={onApplyConfirmed}
      />
    </main>
  );
}

/** Styled link component for back link */
function ButtonLink({
  to,
  className,
  children,
}: {
  to: string;
  className: string;
  children: React.ReactNode;
}) {
  return (
    <Link to={to} className={className}>
      {children}
    </Link>
  );
}
