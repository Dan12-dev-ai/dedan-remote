import { Link } from "react-router-dom";
import type { JobSummary } from "../../types";
import type { ViewMode } from "../../lib/query";
import { readDimensions, scoreBand } from "../../lib/format";
import { SaveButton } from "./SaveButton";
import "./JobCard.css";

interface JobCardProps {
  job: JobSummary;
  /** signed-in user context enables save + preference match display */
  authenticated: boolean;
  saved?: boolean;
  onSaveToggle?: (job: JobSummary, next: boolean) => void;
  index?: number;
  /** Explorer layout: full card, dense row, or score-forward tile. */
  view?: ViewMode;
}

export function JobCard({
  job,
  authenticated,
  saved = false,
  onSaveToggle,
  index = 0,
  view = "list",
}: JobCardProps) {
  const match = job.preference_match;
  const systemScore = Math.round(job.score);
  const explanation = job.score_explanation;
  const band = scoreBand(match ? match.score : systemScore);

  // Dense row view: title, key facts and score only — nothing decorative.
  if (view === "compact") {
    return (
      <article
        className="job-card job-card--compact anim-fade-up"
        style={{ animationDelay: `${Math.min(index, 8) * 45}ms` }}
        data-testid="job-card"
      >
        <div className="job-card__compact-main">
          <Link to={`/opportunities/${job.slug}`} className="job-card__title">
            {job.title}
          </Link>
          <div className="job-card__meta">
            <span className="job-card__company">{job.company}</span>
            <span className="job-card__dot" aria-hidden="true">·</span>
            <span className="badge badge-accent">{job.source_info.name}</span>
            <span className="job-card__fact">{job.location_label}</span>
            {job.salary_disclosed && job.salary && (
              <span className="job-card__fact job-card__fact--salary">
                {job.salary}
              </span>
            )}
          </div>
        </div>
        <div
          className="job-card__compact-score"
          title={
            match
              ? `Preference match ${Math.round(match.score)}/100 — ${match.note}`
              : "Weighted system score (0–100) from the discovery engine"
          }
        >
          <span className={`job-card__score-num job-card__score-num--band-${band.tone}`}>
            {Math.round(match ? match.score : systemScore)}
          </span>
          <span className="job-card__score-unit">
            {match ? "match" : "score"}
          </span>
        </div>
        {authenticated && (
          <SaveButton
            saved={saved}
            onClick={() => onSaveToggle?.(job, !saved)}
            title={job.title}
          />
        )}
      </article>
    );
  }

  return (
    <article
      className={`job-card job-card--${view} anim-fade-up`}
      style={{ animationDelay: `${Math.min(index, 8) * 45}ms` }}
      data-testid="job-card"
    >
      <div className="job-card__top">
        <div className="job-card__heading">
          <Link to={`/opportunities/${job.slug}`} className="job-card__title">
            {job.title}
          </Link>
          <div className="job-card__meta">
            <span className="job-card__company">{job.company}</span>
            <span className="job-card__dot" aria-hidden="true">·</span>
            <span className="badge badge-accent" title="Monitored source">
              {job.source_info.name}
            </span>
          </div>
        </div>
        {authenticated && (
          <SaveButton
            saved={saved}
            onClick={() => onSaveToggle?.(job, !saved)}
            title={job.title}
          />
        )}
      </div>

      <div className="job-card__facts">
        <span className="job-card__fact">
          <span className="job-card__pin" aria-hidden="true">⌖</span>
          {job.location_label}
        </span>
        {job.category && (
          <span className="badge">{formatCategory(job.category)}</span>
        )}
        {job.is_ai_related && (
          <span className="badge badge-accent">AI work</span>
        )}
        {job.experience_hint === "beginner" && (
          <span className="badge badge-success">Entry friendly</span>
        )}
      </div>

      <div className="job-card__stats">
        <div className="job-card__stat">
          <span className="job-card__stat-label">Compensation</span>
          <span className="job-card__stat-value">
            {job.salary_disclosed && job.salary ? (
              job.salary
            ) : (
              <span className="tertiary">Salary not disclosed</span>
            )}
          </span>
        </div>

        <div className="job-card__stat">
          <span className="job-card__stat-label">
            {match ? "Preference match" : "System ranking"}
          </span>
          <div className="job-card__score" title={
            match
              ? match.note
              : "Weighted system score (0–100) from the discovery engine"
          }>
            <span className="job-card__score-ring"
              style={{ "--pct": (match ? match.score : systemScore) } as React.CSSProperties}>
              <span className="job-card__score-num">
                {Math.round(match ? match.score : systemScore)}
              </span>
            </span>
            <span className="job-card__score-unit">{match ? "match" : "score"}</span>
          </div>
        </div>

        <div className="job-card__stat">
          <span className="job-card__stat-label">Freshness</span>
          <span className={`job-card__stat-value${
            job.freshness.is_stale ? " job-card__stale" : ""}`}>
            {job.freshness.label}
          </span>
        </div>
      </div>

      {/* Honest confidence breakdown: basis, numbers and dimensions exactly
          as the engine computed them — no invented certainty. */}
      {(explanation || match) && (
        <details className="job-card__breakdown">
          <summary>
            Why this score?
            <span className={`job-card__band job-card__band--${band.tone}`}>
              {band.word}
            </span>
            <span className="job-card__basis">
              {match ? "based on your preferences" : "system ranking basis"}
            </span>
          </summary>
          <div className="job-card__breakdown-body">
            <p className="job-card__breakdown-lead">
              {match ? (
                <>
                  Preference match <strong>{Math.round(match.score)}/100</strong>{" "}
                  — {match.note} This reflects your saved preferences, not a
                  guarantee of outcomes.
                </>
              ) : explanation ? (
                <>
                  System ranking <strong>{Math.round(explanation.total)}/100</strong>{" "}
                  — {explanation.label}. Weighted from the dimensions below.
                </>
              ) : null}
            </p>

            {explanation && (
              <ul className="job-card__dims">
                {readDimensions(explanation.dimensions)
                  .slice(0, 4)
                  .map((dim) => (
                    <li key={dim.key} className="job-card__dim">
                      <span className="job-card__dim-label" title={dim.meaning}>
                        {dim.label}
                      </span>
                      <span className="job-card__dim-track" aria-hidden="true">
                        <span
                          className={`job-card__dim-fill job-card__dim-fill--${dim.tone}`}
                          style={{ width: `${dim.score}%` }}
                        />
                      </span>
                      <span className="job-card__dim-word">{dim.word}</span>
                    </li>
                  ))}
              </ul>
            )}

            {match && match.reasons.length > 0 && (
              <ul className="job-card__reasons">
                {match.reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            )}

            {explanation && explanation.reasons.length > 0 && (
              <p className="job-card__reasons-note">
                {explanation.reasons[0].detail}
              </p>
            )}
          </div>
        </details>
      )}

      <div className="job-card__footer">
        <Link
          to={`/opportunities/${job.slug}`}
          className="btn btn-secondary btn-sm job-card__cta"
        >
          View opportunity →
        </Link>
      </div>
    </article>
  );
}

function formatCategory(cat: string): string {
  return cat
    .split("-")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

/** Skeleton twin of JobCard — identical dimensions to avoid layout shift. */
export function JobCardSkeleton() {
  return (
    <div className="job-card job-card--skeleton" aria-hidden="true">
      <div className="job-card__top">
        <div style={{ flex: 1 }}>
          <div className="skeleton" style={{ height: 20, width: "68%" }} />
          <div className="skeleton"
            style={{ height: 14, width: "42%", marginTop: 10 }} />
        </div>
        <div className="skeleton"
          style={{ height: 32, width: 32, borderRadius: "50%" }} />
      </div>
      <div className="job-card__facts">
        <div className="skeleton" style={{ height: 22, width: 140 }} />
        <div className="skeleton" style={{ height: 22, width: 74 }} />
        <div className="skeleton" style={{ height: 22, width: 90 }} />
      </div>
      <div className="job-card__stats">
        <div className="skeleton" style={{ height: 44, flex: 1 }} />
        <div className="skeleton" style={{ height: 44, flex: 1 }} />
        <div className="skeleton" style={{ height: 44, flex: 1 }} />
      </div>
      <div className="job-card__footer">
        <div className="skeleton" style={{ height: 32, width: 150 }} />
      </div>
    </div>
  );
}
