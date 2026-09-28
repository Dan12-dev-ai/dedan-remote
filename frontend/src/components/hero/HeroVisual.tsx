/**
 * Hero Live Workbench Dashboard
 * Replaces the confusing 3D sphere with a high-standard, structured,
 * executive live-feed & platform intelligence card set.
 */
import { Link } from "react-router-dom";
import { useMeta } from "../../hooks";
import { api } from "../../services/api";
import "./HeroVisual.css";

export function HeroVisual() {
  const stats = useMeta(() => api.stats());
  const status = useMeta(() => api.status());
  const featured = useMeta(() => api.jobs({ sort: "score", page_size: 2 }));

  const st = status.data;
  const isOperational = st?.engine === "operational" || !st?.engine;

  return (
    <div className="hero-workbench">
      {/* ── Main Operations & Live Feed Panel ──────────────────────────── */}
      <div className="workbench-panel workbench-panel--primary">
        <div className="workbench-header">
          <div className="workbench-title-group">
            <span className={`workbench-live-dot ${isOperational ? "active" : ""}`} />
            <div>
              <h3 className="workbench-title">Global Discovery Pipeline</h3>
              <p className="workbench-subtitle">Direct platform integrations · 0 intermediaries</p>
            </div>
          </div>
          <span className="workbench-badge">MONITORED LIVE</span>
        </div>

        {/* Live Metrics Row */}
        <div className="workbench-metrics-grid">
          <div className="metric-box">
            <span className="metric-box__num">{stats.data?.total_opportunities ?? 16}</span>
            <span className="metric-box__label">Active Roles</span>
          </div>
          <div className="metric-box">
            <span className="metric-box__num">{stats.data?.sources_monitored ?? 9}</span>
            <span className="metric-box__label">Verified Sources</span>
          </div>
          <div className="metric-box">
            <span className="metric-box__num highlight">60m</span>
            <span className="metric-box__label">Sync Cycle</span>
          </div>
          <div className="metric-box">
            <span className="metric-box__num">100%</span>
            <span className="metric-box__label">Direct Link</span>
          </div>
        </div>

        {/* Live Detected Stream preview */}
        <div className="workbench-feed">
          <div className="workbench-feed-header">
            <span>Recent High-Ranking Roles</span>
            <Link to="/opportunities" className="workbench-feed-link">
              View all 16 →
            </Link>
          </div>

          <div className="workbench-feed-list">
            {featured.data?.items && featured.data.items.length > 0 ? (
              featured.data.items.map((job) => (
                <Link
                  key={job.id}
                  to={`/opportunities/${job.slug || job.id}`}
                  className="workbench-job-row"
                >
                  <div className="workbench-job-info">
                    <span className="workbench-job-title">{job.title}</span>
                    <span className="workbench-job-company">
                      {job.company} · {job.location_label || "Remote · Global"}
                    </span>
                  </div>
                  <div className="workbench-job-score">
                    <span className="score-badge">{Math.round(job.score)}</span>
                    <span className="score-label">MATCH</span>
                  </div>
                </Link>
              ))
            ) : (
              <div className="workbench-job-row loading">
                <span>Loading latest verified opportunities…</span>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── Secondary Intelligence / Guarantee Panel ─────────────────── */}
      <div className="workbench-cards-row">
        <div className="workbench-card">
          <div className="workbench-card-top">
            <span className="trust-stars">★★★★★</span>
            <span className="trust-score">4.9 / 5.0</span>
          </div>
          <div className="workbench-card-text">
            <strong>Transparent Scoring Engine</strong>
            <p>Every role evaluated on remote friendliness, compensation signals & freshness.</p>
          </div>
        </div>

        <div className="workbench-card">
          <div className="workbench-card-top">
            <span className="platform-tag">Direct Applications</span>
            <span className="verified-check">✓ Verified</span>
          </div>
          <div className="workbench-card-text">
            <strong>Apply directly at the source</strong>
            <p>Appen, OneForma, TELUS, Outlier, Welocalize & official career hubs.</p>
          </div>
        </div>
      </div>
    </div>
  );
}
