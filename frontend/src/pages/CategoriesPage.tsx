import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../services/api";
import type { CategoryStat } from "../types";

export function CategoriesPage() {
  const [categories, setCategories] = useState<CategoryStat[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.categories()
      .then(setCategories)
      .catch(() => setCategories([]))
      .finally(() => setLoading(false));
  }, []);

  return (
    <main className="container" style={{ padding: "var(--sp-20) 0" }}>
      <header className="anim-fade-up">
        <h1 style={{ fontSize: "var(--text-2xl)", marginBottom: "var(--sp-4)" }}>
          Opportunity Categories
        </h1>
        <p className="muted">
          Derived from tags on real discovered listings.
        </p>
      </header>

      {loading ? (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--sp-3)" }}>
          {Array.from({ length: 12 }).map((_, i) => (
            <div
              key={i}
              className="skeleton"
              style={{ height: 42, width: 120, borderRadius: 999 }}
            />
          ))}
        </div>
      ) : categories.length === 0 ? (
        <p className="muted">
          Categories will appear once the engine discovers tagged opportunities.
        </p>
      ) : (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--sp-3)", marginTop: "var(--sp-8)" }}>
          {categories.map((cat) => (
            <Link
              key={cat.tag}
              to={`/opportunities?tag=${encodeURIComponent(cat.tag)}`}
              className="badge anim-fade-up"
              style={{ padding: "var(--sp-3) var(--sp-5)", fontSize: "var(--text-sm)" }}
            >
              <span>{cat.tag}</span>
              <span className="mono" style={{ opacity: 0.6 }}>
                ({cat.count})
              </span>
            </Link>
          ))}
        </div>
      )}
    </main>
  );
}
