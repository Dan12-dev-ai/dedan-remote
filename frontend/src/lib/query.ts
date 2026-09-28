/**
 * Explorer state ⇄ URL search params.
 *
 * The URL is the canonical source of truth for what the user is looking at:
 * every filter, the sort order, the view mode and the page number round-trip
 * through the query string. That yields shareable links, working browser
 * back/forward, and a single place where filter semantics are defined.
 *
 * Only parameters the API actually accepts are emitted, and defaults are
 * dropped so canonical URLs stay short.
 */

import type { JobFilters } from "../types";

export type ViewMode = "list" | "compact" | "visual";

export const VIEW_MODES: readonly ViewMode[] = ["list", "compact", "visual"];

export const DEFAULT_FILTERS: JobFilters = {
  sort: "score",
  page: 1,
  page_size: 20,
};

/** Sort options exactly as the API defines them (`SortOption`). */
export const SORT_OPTIONS: readonly {
  value: NonNullable<JobFilters["sort"]>;
  label: string;
  hint: string;
}[] = [
  {
    value: "best_match",
    label: "Best match",
    hint: "Query relevance first, then system ranking",
  },
  {
    value: "score",
    label: "Ranking score",
    hint: "Weighted 0–100 discovery-engine score",
  },
  {
    value: "newest",
    label: "Newest",
    hint: "Most recently discovered first",
  },
  {
    value: "freshness",
    label: "Freshness",
    hint: "Most recently posted at the source",
  },
  {
    value: "salary",
    label: "Compensation",
    hint: "Listings that publish a pay rate first",
  },
];

export const PAGE_SIZES = [20, 30, 50] as const;

const SORT_VALUES = new Set<string>(SORT_OPTIONS.map((o) => o.value));

function readNumber(params: URLSearchParams, key: string): number | undefined {
  const raw = params.get(key);
  if (raw === null || raw === "") return undefined;
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function readFlag(params: URLSearchParams, key: string): boolean | undefined {
  return params.get(key) === "1" ? true : undefined;
}

export function parseView(raw: string | null): ViewMode {
  return VIEW_MODES.includes(raw as ViewMode) ? (raw as ViewMode) : "list";
}

/** Build explorer filters from a URL query string. */
export function filtersFromParams(params: URLSearchParams): JobFilters {
  const sortRaw = params.get("sort");
  const pageSize = readNumber(params, "page_size");
  const page = readNumber(params, "page");

  return {
    ...DEFAULT_FILTERS,
    q: params.get("q") || undefined,
    source: params.get("source") || undefined,
    category: params.get("category") || undefined,
    tag: params.get("tag") || undefined,
    country: params.get("country") || undefined,
    worldwide: readFlag(params, "worldwide"),
    remote: readFlag(params, "remote"),
    beginner: readFlag(params, "beginner"),
    ai: readFlag(params, "ai"),
    has_salary: readFlag(params, "has_salary"),
    min_score: readNumber(params, "min_score"),
    max_age_days: readNumber(params, "max_age_days"),
    sort:
      sortRaw && SORT_VALUES.has(sortRaw)
        ? (sortRaw as JobFilters["sort"])
        : DEFAULT_FILTERS.sort,
    page: page && page > 0 ? page : 1,
    page_size: pageSize && pageSize > 0 ? pageSize : DEFAULT_FILTERS.page_size,
  };
}

/**
 * Serialize explorer state back into a query string.
 *
 * Defaults are dropped, so the canonical URL for an unfiltered explorer is
 * just `/opportunities`.
 */
export function paramsFromFilters(
  filters: JobFilters,
  view: ViewMode = "list",
): URLSearchParams {
  const params = new URLSearchParams();

  const set = (key: string, value: unknown) => {
    if (
      value === undefined ||
      value === null ||
      value === "" ||
      value === false
    ) {
      return;
    }
    params.set(key, String(value));
  };

  set("q", filters.q?.trim());
  set("source", filters.source);
  set("category", filters.category);
  set("tag", filters.tag);
  set("country", filters.country);
  if (filters.worldwide) params.set("worldwide", "1");
  if (filters.remote) params.set("remote", "1");
  if (filters.beginner) params.set("beginner", "1");
  if (filters.ai) params.set("ai", "1");
  if (filters.has_salary) params.set("has_salary", "1");
  set("min_score", filters.min_score);
  set("max_age_days", filters.max_age_days);

  if (filters.sort && filters.sort !== DEFAULT_FILTERS.sort) {
    params.set("sort", filters.sort);
  }
  if (filters.page && filters.page > 1) {
    params.set("page", String(filters.page));
  }
  if (filters.page_size && filters.page_size !== DEFAULT_FILTERS.page_size) {
    params.set("page_size", String(filters.page_size));
  }
  if (view !== "list") params.set("view", view);

  return params;
}

/**
 * Apply a partial filter change and reset to page 1.
 *
 * Any change to *what* is being queried invalidates the current page, so
 * pagination resets automatically — unless the change is the page itself.
 */
export function applyFilters(
  current: JobFilters,
  patch: Partial<JobFilters>,
): JobFilters {
  const changesResultSet = Object.keys(patch).some(
    (key) => key !== "page" && key !== "page_size",
  );
  return {
    ...current,
    ...patch,
    page: patch.page ?? (changesResultSet ? 1 : current.page),
  };
}

/** A human-readable chip describing one active filter. */
export interface ActiveFilter {
  key: keyof JobFilters;
  label: string;
  value: string;
}

const FRESHNESS_PRESETS: Record<number, string> = {
  1: "last 24 hours",
  7: "last week",
  30: "last 30 days",
  90: "last 90 days",
  365: "last year",
};

/** Every filter the user has actually set, for the active-filters row. */
export function activeFilters(filters: JobFilters): ActiveFilter[] {
  const out: ActiveFilter[] = [];
  const push = (key: keyof JobFilters, label: string, value: string) =>
    out.push({ key, label, value });

  if (filters.q?.trim()) push("q", "Search", `“${filters.q.trim()}”`);
  if (filters.source) push("source", "Source", filters.source);
  if (filters.category) push("category", "Category", filters.category);
  if (filters.tag) push("tag", "Tag", filters.tag);
  if (filters.country) push("country", "Country", filters.country);
  if (filters.remote) push("remote", "Remote", "only");
  if (filters.worldwide) push("worldwide", "Worldwide", "only");
  if (filters.ai) push("ai", "AI", "related only");
  if (filters.beginner) push("beginner", "Level", "entry friendly");
  if (filters.has_salary) push("has_salary", "Compensation", "disclosed");
  if (filters.min_score !== undefined) {
    push("min_score", "Min score", String(filters.min_score));
  }
  if (filters.max_age_days !== undefined) {
    push(
      "max_age_days",
      "Freshness",
      FRESHNESS_PRESETS[filters.max_age_days] ??
        `within ${filters.max_age_days} days`,
    );
  }
  return out;
}

export function countActiveFilters(filters: JobFilters): number {
  return activeFilters(filters).length;
}

/** Clearing a filter key restores its default. */
export function clearFilter(
  filters: JobFilters,
  key: keyof JobFilters,
): JobFilters {
  return applyFilters(filters, { [key]: undefined } as Partial<JobFilters>);
}

/** Reset every filter, keeping how results are ordered and sized. */
export function clearAllFilters(filters: JobFilters): JobFilters {
  return {
    ...DEFAULT_FILTERS,
    sort: filters.sort,
    page_size: filters.page_size,
  };
}
