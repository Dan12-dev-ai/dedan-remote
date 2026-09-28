import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { SearchBar } from "../components/search/SearchBar";
import { JobCard, JobCardSkeleton } from "../components/jobs/JobCard";
import { useDebounce, useMeta } from "../hooks";
import { api } from "../services/api";
import { useAuth } from "../stores/AuthContext";
import type { CategoryStat, SourceStat } from "../types";
import { AIDoctor } from "../components/cinematic/AIDoctor";
import { HeroShell } from "../components/hero";

/**
 * Landing page — cinematic hero, truthful live discovery statistics,
 * categories, featured opportunities, how-it-works, source ecosystem.
 * Every number comes from /api/stats, /api/sources, /api/categories.
 */
export function LandingPage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [search, setSearch] = useState("");
  const debounced = useDebounce(search, 400);

  const stats = useMeta(() => api.stats());
  const status = useMeta(() => api.status());
  const sources = useMeta(() => api.sources());
  const categories = useMeta(() => api.categories());
  const featured = useMeta(() =>
    api.jobs({ sort: "score", page_size: 3 }),
  );

  // Push hero search to the explorer (canonical query params).
  useEffect(() => {
    if (!debounced.trim()) return;
    const t = window.setTimeout(() => {
      navigate(`/opportunities?q=${encodeURIComponent(debounced.trim())}`);
    }, 250);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced]);

  const s = stats.data;
  const st = status.data;

  const engineLabel =
    st?.engine === "operational"
      ? "Operational"
      : st?.engine === "degraded"
        ? "Degraded"
        : st?.engine === "idle"
          ? "Standby"
          : "…";
  const engineClass =
    st?.engine === "operational"
      ? "ok"
      : st?.engine === "degraded"
        ? "warn"
        : "idle";

  return (
    <main className="landing">
      {/* ── 1. Cinematic hero (FULL-BLEED) ─────────────────────────────────────── */}
      <section className="full-bleed full-bleed--height-auto" aria-labelledby="hero-title">
        {/* Background visual layer */}
        <div className="full-bleed__content">
          {/* HeroShell owns its own content composition — nothing to pass. */}
          <HeroShell />
        </div>
      </section>

      {/* ── 2. Featured opportunities (WIDE) ───────────────────────────────────── */}
      <section className="section section--wide" aria-labelledby="featured-heading">
        <header className="cluster cluster--horizontal">
          <h2 id="featured-heading">Featured opportunities</h2>
          <p className="muted">
            Live examples from the discovery feed. Every listing links directly
            to the source — no scraping, no intermediaries.
          </p>
        </header>
        {/* `api.jobs` returns a paginated page, not a bare array — read `items`
            so a change to the page size can never silently change this band. */}
        {featured.data && featured.data.items.length > 0 ? (
          <div className="cluster cluster--vertical cards">
            {featured.data.items.map((job, index) => (
              <JobCard
                key={job.id}
                job={job}
                view="compact"
                index={index}
                authenticated={!!user}
              />
            ))}
          </div>
        ) : (
          <div className="cluster cluster--vertical cards">
            {Array.from({ length: 3 }).map((_, i) => (
              <JobCardSkeleton key={i} />
            ))}
          </div>
        )}
      </section>

      {/* ── 3. Categories (STANDARD GRID) ──────────────────────────────────────── */}
      <section className="section section--standard" aria-labelledby="categories-heading">
        <header>
          <h2 id="categories-heading">Opportunity categories</h2>
          <p className="muted">
            High-level groupings the discovery engine uses to organize findings.
          </p>
        </header>
        {categories.data && categories.data.length > 0 ? (
          /* `CategoryStat` is `{ tag, count }` — the engine classifies listings
             by tag, so there is no display name or description to show. */
          <div className="landing__cats">
            {categories.data.map((cat: CategoryStat) => (
              <Link
                key={cat.tag}
                to={`/opportunities?tag=${encodeURIComponent(cat.tag)}`}
                className="landing__cat"
              >
                <span>{cat.tag}</span>
                <span className="landing__cat-count mono">{cat.count}</span>
              </Link>
            ))}
          </div>
        ) : (
          <div className="landing__cats-skeleton" aria-hidden="true">
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="skeleton" style={{ height: 38, width: 132 }} />
            ))}
          </div>
        )}
      </section>

      {/* ── 4. How it works (STANDARD WIDTH) ──────────────────────────────────── */}
      <section className="section section--standard" aria-labelledby="how-it-works-heading">
        <header>
          <h2 id="how-it-works-heading">How DEDAN Remote works</h2>
        </header>
        <ol className="landing__steps">
          <li>
            <span className="landing__step-num">01</span>
            <h3>Automated discovery</h3>
            <p>
              Scrapers monitor {s?.sources_monitored ?? "multiple"} platforms
              on a {s?.discovery_interval_minutes ?? "—"}-minute cycle,
              deduplicating every listing it finds.
            </p>
          </li>
          <li>
            <span className="landing__step-num">02</span>
            <h3>Transparent ranking</h3>
            <p>
              Each opportunity is scored 0–100 on remote-friendliness,
              compensation signals, AI relevance, freshness and more — every
              score is explainable.
            </p>
          </li>
          <li>
            <span className="landing__step-num">03</span>
            <h3>Search & filter</h3>
            <p>
              Find roles by keyword, source, category, region, freshness and
              eligibility — every filter runs against real listing data.
            </p>
          </li>
          <li>
            <span className="landing__step-num">04</span>
            <h3>Apply at the source</h3>
            <p>
              Applications continue on the official platform. DEDAN Remote
              helps you discover and track — the source handles hiring.
            </p>
          </li>
        </ol>
      </section>

      {/* ── 5. Source ecosystem (WIDE GRID) ───────────────────────────────────── */}
      <section className="section section--wide" aria-labelledby="sources-heading">
        <header className="cluster cluster--horizontal">
          <h2 id="sources-heading">Monitored source ecosystem</h2>
          <p className="muted">
            Platforms the discovery engine watches. Listings are linked to
            their original source; no endorsement implied.
          </p>
        </header>
        {sources.data && sources.data.length > 0 ? (
          <div className="landing__sources">
            {sources.data.map((src: SourceStat) => (
              <Link key={src.id} to="/sources" className="landing__source">
                <span className="landing__source-name">{src.name}</span>
                <span className="landing__source-meta">
                  <span
                    className={`landing__source-dot ${src.circuit_open ? "off" : "on"}`}
                    aria-hidden="true"
                  />
                  {!src.monitored
                    ? "not monitored"
                    : src.circuit_open
                      ? "paused"
                      : src.last_success
                        ? "monitored"
                        : "registered"}
                  <span className="tertiary"> · {src.job_count} found</span>
                </span>
              </Link>
            ))}
          </div>
        ) : (
          <p className="muted">Source registry loading…</p>
        )}
      </section>

      {/* ── 6. Personalized discovery (COMPACT) ─────────────────────────────── */}
      <section className="section section--compact" aria-labelledby="personalized-heading">
        <div className="landing__personal glass">
          <div>
            <h2 id="personalized-heading">Make discovery personal</h2>
            <p className="muted">
              Save preferences for categories, experience level and region —
              DEDAN Remote ranks opportunities against them with transparent,
              deterministic rules. No black-box claims.
            </p>
          </div>
          <Link to="/profile" className="btn btn-primary">
            Set preferences
          </Link>
        </div>
      </section>
    </main>
  );
}
