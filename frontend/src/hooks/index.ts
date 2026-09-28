import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../services/api";
import type { JobFilters, Page } from "../types";
import { api } from "../services/api";

/** Debounce a rapidly-changing value (search inputs). */
export function useDebounce<T>(value: T, delayMs = 350): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(t);
  }, [value, delayMs]);
  return debounced;
}

interface JobsState {
  data: Page | null;
  loading: boolean;
  error: ApiError | null;
  reload: () => void;
}

/**
 * Fetches a jobs page whenever filters change; cancels stale requests so
 * fast typing never shows out-of-order results.
 */
export function useJobs(filters: JobFilters): JobsState {
  const [data, setData] = useState<Page | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [version, setVersion] = useState(0);
  const reqId = useRef(0);

  const key = JSON.stringify(filters);

  useEffect(() => {
    const id = ++reqId.current;
    setLoading(true);
    setError(null);
    api
      .jobs(filters)
      .then((page) => {
        if (reqId.current === id) setData(page);
      })
      .catch((err: unknown) => {
        if (reqId.current !== id) return;
        setError(
          err instanceof ApiError
            ? err
            : new ApiError(0, "unknown", "Something went wrong."),
        );
      })
      .finally(() => {
        if (reqId.current === id) setLoading(false);
      });
    // filters is captured via `key` serialization
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, version]);

  const reload = useCallback(() => setVersion((v) => v + 1), []);
  return { data, loading, error, reload };
}

/** Simple match-media hook for deliberate responsive behavior. */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() =>
    typeof window !== "undefined"
      ? window.matchMedia(query).matches
      : false,
  );
  useEffect(() => {
    const mql = window.matchMedia(query);
    const onChange = (e: MediaQueryListEvent) => setMatches(e.matches);
    mql.addEventListener("change", onChange);
    setMatches(mql.matches);
    return () => mql.removeEventListener("change", onChange);
  }, [query]);
  return matches;
}

/** Generic GET-once hook with retry for meta endpoints. */
export function useMeta<T>(fetcher: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    fetcher()
      .then((d) => alive && setData(d))
      .catch((err: unknown) => {
        if (!alive) return;
        setError(
          err instanceof ApiError
            ? err
            : new ApiError(0, "unknown", "Something went wrong."),
        );
      })
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version]);

  const reload = useCallback(() => setVersion((v) => v + 1), []);
  return { data, loading, error, reload };
}
