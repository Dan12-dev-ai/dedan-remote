import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { SearchBar } from "../components/search/SearchBar";
import { FilterSidebar } from "../components/filters/FilterSidebar";
import { JobCard, JobCardSkeleton } from "../components/jobs/JobCard";
import { EmptyState, ErrorState } from "../components/common/States";
import { useDebounce, useJobs, useMediaQuery, useMeta } from "../hooks";
import { api } from "../services/api";
import { parseView, type ViewMode } from "../lib/query";
import { useAuth } from "../stores/AuthContext";
import type { JobFilters, JobSummary, SourceStat } from "../types";

const DEFAULTS: JobFilters = { sort: "best_match", page: 1, page_size: 12 };

const VIEW_OPTIONS: readonly { value: ViewMode; label: string; title: string }[] = [
  { value: "list", label: "List", title: "Full cards" },
  { value: "compact", label: "Compact", title: "Dense rows" },
  { value: "visual", label: "Visual", title: "Score-forward tiles" },
];

/** URL search params ⇄ filter state (shareable, back-button friendly). */
function filtersFromParams(params: URLSearchParams): JobFilters {
  const out: JobFilters = { ...DEFAULTS };
  const get = (k: string) => params.get(k) || undefined;
  out.q = get("q");
  out.source = get("source");
  out.category = get("category");
  out.tag = get("tag");
  out.min_score = get("min_score") ? Number(get("min_score")) : undefined;
  out.max_age_days = get("max_age_days")
    ? Number(get("max_age_days"))
    : undefined;
  out.worldwide = params.get("worldwide") === "1" || undefined;
  out.remote = params.get("remote") === "1" || undefined;
  out.beginner = params.get("beginner") === "1" || undefined;
  out.ai = params.get("ai") === "1" || undefined;
  out.has_salary = params.get("has_salary") === "1" || undefined;
  const sort = get("sort");
  if (sort) out.sort = sort as JobFilters["sort"];
  out.page = params.get("page") ? Number(params.get("page")) : 1;
  return out;
}

function paramsFromFilters(
  filters: JobFilters,
  view: ViewMode = "list",
): URLSearchParams {
  const p = new URLSearchParams();
  const set = (k: string, v: unknown) => {
    if (v !== undefined && v !== null && v !== "" && v !== false) {
      p.set(k, String(v));
    }
  };
  set("q", filters.q);
  set("source", filters.source);
  set("category", filters.category);
  set("tag", filters.tag);
  set("min_score", filters.min_score);
  set("max_age_days", filters.max_age_days);
  if (filters.worldwide) p.set("worldwide", "1");
  if (filters.remote) p.set("remote", "1");
  if (filters.beginner) p.set("beginner", "1");
  if (filters.ai) p.set("ai", "1");
  if (filters.has_salary) p.set("has_salary", "1");
  if (filters.sort && filters.sort !== "best_match") p.set("sort", filters.sort);
  if (filters.page && filters.page > 1) p.set("page", String(filters.page));
  if (view !== "list") p.set("view", view);
  return p;
}

export function OpportunitiesPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { user } = useAuth();
  const isDesktop = useMediaQuery("(min-width: 980px)");

  const urlFilters = useMemo(
    () => filtersFromParams(searchParams),
    [searchParams],
  );
  // `view` comes from the URL; `setView` keeps the local presentation in sync
  // while the URL update effect below writes it back as the canonical source.
  const [view, setView] = useState<ViewMode>(parseView(searchParams.get("view")));
  const [searchText, setSearchText] = useState(urlFilters.q ?? "");
  const debouncedSearch = useDebounce(searchText, 350);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [filters, setFilters] = useState(urlFilters);

  // Sync URL params with internal state
  useEffect(() => {
    setFilters(urlFilters);
  }, [urlFilters]);

  // Update URL when filters change
  useEffect(() => {
    setSearchParams(paramsFromFilters(filters, view), { replace: false });
  }, [filters, view, setSearchParams]);

  // Push hero search to the explorer (canonical query params).
  useEffect(() => {
    if (!debouncedSearch.trim()) return;
    const t = window.setTimeout(() => {
      setFilters((prev) => ({ ...prev, q: debouncedSearch.trim(), page: 1 }));
    }, 250);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch]);

  const {
    data: jobData,
    error: jobError,
    loading: jobLoading,
    reload: reloadJobs,
  } = useJobs(filters);
  const {
    data: sources,
  } = useMeta(() => api.sources());
  const {
    data: savedItems = [],
  } = useMeta(() => api.saved());

  const savedIds = new Set((savedItems ?? []).map((item) => item.job.id));

  const handleSaveToggle = (job: JobSummary, next: boolean) => {
    if (next) {
      api.saveJob(job.id);
    } else {
      api.unsaveJob(job.id);
    }
  };

  const clearFilters = () => {
    setFilters({ ...DEFAULTS, q: filters.q });
  };

  // `!!x + !!y` coerces to boolean before adding, which is a TypeError in
  // strict mode. Map to 0/1 explicitly so the count is a number.
  const activeFilterCount =
    (filters.source ? 1 : 0) +
    (filters.category ? 1 : 0) +
    (filters.tag ? 1 : 0) +
    (filters.min_score ? 1 : 0) +
    (filters.max_age_days ? 1 : 0) +
    (filters.worldwide ? 1 : 0) +
    (filters.remote ? 1 : 0) +
    (filters.beginner ? 1 : 0) +
    (filters.ai ? 1 : 0) +
    (filters.has_salary ? 1 : 0);

  const updateFilters = (updates: Partial<JobFilters>) => {
    setFilters((prev) => ({ ...prev, ...updates, page: 1 }));
  };

  const handleViewChange = (newView: ViewMode) => {
    setView(newView);
  };

  const isEmpty =
    !jobLoading && !jobError && jobData && jobData.items.length === 0;
  const isError = !!jobError;
  const isLoading = jobLoading;

  return (
    <main className="opportunities">
      {/* ── Page Header (STANDARD WIDTH) ───────────────────────────────────── */}
      <section className="section section--standard">
        <div className="cluster cluster--horizontal">
          <div className="stack">
            <h1 className="explorer__title">Explore opportunities</h1>
            <p className="explorer__subtitle">
              {jobData?.total ?? 0} opportunities found
            </p>
          </div>
          
          {/* Search - takes remaining space */}
          <div className="explorer__search">
            <SearchBar
              value={searchText}
              onChange={setSearchText}
              size="page"
              placeholder="Search opportunities…"
            />
          </div>
        </div>
      </section>

      {/* ── Main Layout (DISTRIBUTED COMPOSITION) ──────────────────────────── */}
      <section className="section section--wide">
        {/* Distributed layout: Sidebar + Main Content */}
        <div className="split-layout split-layout--standard">
          {/* LEFT: Filters Sidebar (CONDENSED ON DESKTOP, FLOATING ON MOBILE) */}
          <div className="split-layout__sidebar">
            {/* Desktop: Sticky sidebar */}
            {isDesktop && (
              <div className="sticky-panel sticky-panel--top">
                <FilterSidebar
                  filters={filters}
                  sources={sources ?? []}
                  onChange={updateFilters}
                  onClear={clearFilters}
                  activeCount={activeFilterCount}
                />
              </div>
            )}
            
            {/* Mobile: Filter button in header (handled above) */}
            {!isDesktop && (
              <div className="explorer__mobile-bar">
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => setDrawerOpen(true)}
                >
                  Filters
                </button>
                
                {/* View toggle */}
                <div className="explorer__views">
                  {VIEW_OPTIONS.map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      className={`explorer__view ${view === option.value ? "explorer__view--active" : ""}`}
                      onClick={() => handleViewChange(option.value)}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* RIGHT: Main Content Area */}
          <div className="split-layout__content">
            {/* Results Header */}
            {!isDesktop && (
              <div className="explorer__header">
                <div className="stack">
                  <h1 className="explorer__title">Explore opportunities</h1>
                  <p className="explorer__subtitle">
                    {jobData?.total ?? 0} opportunities found
                  </p>
                </div>
                
                {/* Search - takes remaining space on mobile */}
                <div className="explorer__search">
                  <SearchBar
                    value={searchText}
                    onChange={setSearchText}
                    size="page"
                    placeholder="Search opportunities…"
                  />
                </div>
                
                {/* View toggle on mobile */}
                <div className="explorer__views">
                  {VIEW_OPTIONS.map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      className={`explorer__view ${view === option.value ? "explorer__view--active" : ""}`}
                      onClick={() => handleViewChange(option.value)}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
              </div>
            )}
            
            {/* Results Area */}
            <div className="explorer__results">
              {isError ? (
                <ErrorState onRetry={reloadJobs} />
              ) : isLoading ? (
                <JobCardSkeleton />
              ) : isEmpty ? (
                <EmptyState
                  title="No opportunities match these filters"
                  description="Filters are applied against live listing data. Widening the score range or clearing the source filter usually surfaces results."
                  action={
                    <button
                      type="button"
                      className="btn btn--secondary"
                      onClick={clearFilters}
                    >
                      Clear filters
                    </button>
                  }
                />
              ) : (
                <>
                  {/* Results Grid - using appropriate layout based on view */}
                  <div className={`explorer__grid explorer__grid--${view}`}>
                    {jobData?.items.map((job, index) => (
                      <JobCard
                        key={job.id}
                        job={job}
                        view={view}
                        index={index}
                        authenticated={!!user}
                        saved={savedIds.has(job.id)}
                        onSaveToggle={handleSaveToggle}
                      />
                    ))}
                    
                    {/* Loading skeletons for smoother experience */}
                    {jobLoading && (
                      <>
                        <JobCardSkeleton />
                        <JobCardSkeleton />
                        <JobCardSkeleton />
                      </>
                    )}
                  </div>
                  
                  {/* Pagination */}
                  {jobData?.pages && jobData.pages > 1 && (
                    <nav className="explorer__pager" aria-label="Pagination">
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        disabled={!jobData?.has_prev || jobLoading}
                        onClick={() =>
                          updateFilters({ page: (jobData?.page ?? 1) - 1 })
                        }
                      >
                        ← Previous
                      </button>
                      <span className="explorer__pager-info mono">
                        Page {jobData?.page} / {jobData?.pages}
                      </span>
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        disabled={!jobData?.has_next || jobLoading}
                        onClick={() =>
                          updateFilters({ page: (jobData?.page ?? 1) + 1 })
                        }
                      >
                        Next →
                      </button>
                    </nav>
                  )}
                </>
              )}
            </div>
          </div>
        </div>
      </section>

      {/* ── Mobile Filter Drawer (FLOATING PANEL) ────────────────────────────── */}
      {!isDesktop && drawerOpen && (
        <div className="explorer__drawer" role="dialog" aria-label="Filters">
          <div className="explorer__drawer-panel">
            <div className="explorer__drawer-head">
              <span>Filters</span>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => setDrawerOpen(false)}
              >
                Done
              </button>
            </div>
            <FilterSidebar
              filters={filters}
              sources={sources ?? []}
              onChange={updateFilters}
              onClear={clearFilters}
              activeCount={activeFilterCount}
            />
          </div>
        </div>
      )}
    </main>
  );
}
