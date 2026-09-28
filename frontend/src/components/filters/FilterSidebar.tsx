import type { JobFilters, SourceStat } from "../../types";
import "./Filters.css";

interface FilterSidebarProps {
  filters: JobFilters;
  onChange: (patch: Partial<JobFilters>) => void;
  sources: SourceStat[];
  onClear: () => void;
  activeCount: number;
}

const CATEGORIES = [
  { value: "ai-ml", label: "AI / ML" },
  { value: "software", label: "Software" },
  { value: "data", label: "Data" },
  { value: "ai-training", label: "AI Training" },
  { value: "research", label: "Research" },
  { value: "general-remote", label: "General Remote" },
];

const SORTS: { value: NonNullable<JobFilters["sort"]>; label: string;
  hint: string }[] = [
  { value: "best_match", label: "Best match",
    hint: "Title/tag relevance first, then system score" },
  { value: "score", label: "Highest score",
    hint: "System ranking score (0–100)" },
  { value: "newest", label: "Newest",
    hint: "Most recently discovered by the engine" },
  { value: "freshness", label: "Freshness",
    hint: "Posted date when known, else discovery date" },
  { value: "salary", label: "Salary listed",
    hint: "Opportunities with disclosed compensation first" },
];

const FRESHNESS_OPTIONS = [
  { value: undefined as number | undefined, label: "Any time" },
  { value: 7, label: "Past week" },
  { value: 30, label: "Past month" },
  { value: 90, label: "Past 3 months" },
];

function countActive(filters: JobFilters): number {
  let n = 0;
  if (filters.source) n++;
  if (filters.category) n++;
  if (filters.worldwide) n++;
  if (filters.remote) n++;
  if (filters.beginner) n++;
  if (filters.ai) n++;
  if (filters.has_salary) n++;
  if (filters.min_score) n++;
  if (filters.max_age_days) n++;
  return n;
}

export function FilterSidebar({
  filters,
  onChange,
  sources,
  onClear,
  activeCount,
}: FilterSidebarProps) {
  const active = activeCount || countActive(filters);

  return (
    <aside className="filters" aria-label="Filter opportunities">
      <div className="filters__head">
        <h2 className="filters__title">Filters</h2>
        {active > 0 && (
          <button type="button" className="filters__clear"
            onClick={onClear}>
            Clear ({active})
          </button>
        )}
      </div>

      <fieldset className="filters__group">
        <legend className="filters__legend">Sort by</legend>
        <select
          className="input"
          value={filters.sort ?? "best_match"}
          aria-label="Sort opportunities"
          onChange={(e) =>
            onChange({ sort: e.target.value as JobFilters["sort"] })
          }
        >
          {SORTS.map((s) => (
            <option key={s.value} value={s.value}>{s.label}</option>
          ))}
        </select>
        <p className="filters__hint">
          {SORTS.find((s) => s.value === (filters.sort ?? "best_match"))
            ?.hint}
        </p>
      </fieldset>

      <fieldset className="filters__group">
        <legend className="filters__legend">Category</legend>
        <div className="filters__chips" role="group">
          {CATEGORIES.map((c) => (
            <button
              key={c.value}
              type="button"
              className={`chip${
                filters.category === c.value ? " chip--on" : ""}`}
              aria-pressed={filters.category === c.value}
              onClick={() =>
                onChange({
                  category: filters.category === c.value ? undefined
                    : c.value,
                })
              }
            >
              {c.label}
            </button>
          ))}
        </div>
      </fieldset>

      <fieldset className="filters__group">
        <legend className="filters__legend">Eligibility</legend>
        <label className="toggle">
          <input type="checkbox" checked={!!filters.worldwide}
            onChange={(e) => onChange({ worldwide: e.target.checked || undefined })} />
          <span>Worldwide / no country limit</span>
        </label>
        <label className="toggle">
          <input type="checkbox" checked={!!filters.remote}
            onChange={(e) => onChange({ remote: e.target.checked || undefined })} />
          <span>Remote only</span>
        </label>
        <label className="toggle">
          <input type="checkbox" checked={!!filters.beginner}
            onChange={(e) => onChange({ beginner: e.target.checked || undefined })} />
          <span>Beginner friendly</span>
        </label>
        <label className="toggle">
          <input type="checkbox" checked={!!filters.ai}
            onChange={(e) => onChange({ ai: e.target.checked || undefined })} />
          <span>AI-related work</span>
        </label>
        <label className="toggle">
          <input type="checkbox" checked={!!filters.has_salary}
            onChange={(e) =>
              onChange({ has_salary: e.target.checked || undefined })} />
          <span>Salary disclosed</span>
        </label>
      </fieldset>

      <fieldset className="filters__group">
        <legend className="filters__legend">Minimum system score</legend>
        <input
          type="range"
          min={0}
          max={90}
          step={10}
          value={filters.min_score ?? 0}
          aria-label="Minimum system score"
          onChange={(e) =>
            onChange({
              min_score: Number(e.target.value) > 0
                ? Number(e.target.value)
                : undefined,
            })
          }
        />
        <div className="filters__range-labels">
          <span>Any</span>
          <span className="mono">{filters.min_score ?? 0}+</span>
        </div>
      </fieldset>

      <fieldset className="filters__group">
        <legend className="filters__legend">Freshness</legend>
        <select
          className="input"
          aria-label="Maximum age"
          value={filters.max_age_days ?? ""}
          onChange={(e) =>
            onChange({
              max_age_days: e.target.value
                ? Number(e.target.value)
                : undefined,
            })
          }
        >
          {FRESHNESS_OPTIONS.map((o) => (
            <option key={o.label} value={o.value ?? ""}>{o.label}</option>
          ))}
        </select>
      </fieldset>

      <fieldset className="filters__group">
        <legend className="filters__legend">Source</legend>
        <select
          className="input"
          aria-label="Source platform"
          value={filters.source ?? ""}
          onChange={(e) =>
            onChange({ source: e.target.value || undefined })
          }
        >
          <option value="">All sources</option>
          {sources.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name} ({s.job_count})
            </option>
          ))}
        </select>
      </fieldset>
    </aside>
  );
}
