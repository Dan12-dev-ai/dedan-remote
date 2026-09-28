import { Link } from "react-router-dom";

export function AboutPage() {
  return (
    <main className="container" style={{ padding: "var(--sp-20) 0" }}>
      <header className="anim-fade-up">
        <h1 style={{ fontSize: "var(--text-2xl)", marginBottom: "var(--sp-4)" }}>
          About DEDAN Remote
        </h1>
        <p className="muted" style={{ fontSize: "var(--text-lg)" }}>
          Intelligent global opportunity discovery powered by automated scraping and transparent ranking.
        </p>
      </header>

      <section className="glass anim-fade-up" style={{ padding: "var(--sp-8)", marginTop: "var(--sp-8)" }}>
        <h2 style={{ marginBottom: "var(--sp-4)" }}>How it works</h2>
        <ol style={{ display: "flex", flexDirection: "column", gap: "var(--sp-6)" }}>
          <li>
            <strong>Automated discovery</strong>
            <p className="muted">
              Scrapers monitor multiple platforms on a regular cycle, deduplicating every listing found.
            </p>
          </li>
          <li>
            <strong>Transparent ranking</strong>
            <p className="muted">
              Each opportunity is scored 0–100 on remote-friendliness, compensation signals, AI relevance, freshness and more.
            </p>
          </li>
          <li>
            <strong>Search & filter</strong>
            <p className="muted">
              Find roles by keyword, source, category, region, freshness and eligibility.
            </p>
          </li>
          <li>
            <strong>Apply at the source</strong>
            <p className="muted">
              Applications continue on the official platform. DEDAN Remote helps you discover and track.
            </p>
          </li>
        </ol>
      </section>

      <section style={{ marginTop: "var(--sp-12)" }}>
        <h2 style={{ marginBottom: "var(--sp-4)" }}>Get started</h2>
        <Link to="/opportunities" className="btn btn-primary">
          Explore opportunities →
        </Link>
      </section>
    </main>
  );
}
