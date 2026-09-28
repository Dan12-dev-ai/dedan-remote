import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../../services/api";
import { useDebounce } from "../../hooks";
import { useEscape, useFocusTrap, useScrollLock } from "../../lib/motion";
import type { SearchResponse } from "../../types";
import "./CommandPalette.css";

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
}

interface PaletteItem {
  id: string;
  group: string;
  label: string;
  hint?: string;
  run: () => void;
}

/** Static destinations — every route registered in App.tsx. */
const NAV_COMMANDS: readonly { label: string; to: string; hint?: string }[] = [
  { label: "Explore opportunities", to: "/opportunities", hint: "Search & filters" },
  { label: "Saved opportunities", to: "/saved" },
  { label: "Applications", to: "/applications" },
  { label: "Recommendations", to: "/recommendations" },
  { label: "Sources", to: "/sources" },
  { label: "Categories", to: "/categories" },
  { label: "About DEDAN Remote", to: "/about" },
];

/**
 * Command palette (⌘K / Ctrl+K).
 *
 * Searches the live index through `api.search` — no client-side catalogue, so
 * results are exactly what the backend has. Navigation commands are static and
 * labelled as such; nothing here invents data the API didn't return.
 */
export function CommandPalette({ open, onClose }: CommandPaletteProps) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const debouncedQuery = useDebounce(query, 250);
  const [results, setResults] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);

  const inputRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const panelRef = useFocusTrap(open);
  const reqId = useRef(0);

  useScrollLock(open);
  useEscape(open, onClose);

  // Fresh start each time the palette opens.
  useEffect(() => {
    if (open) {
      setQuery("");
      setResults(null);
      setError(null);
      setActiveIndex(0);
      window.setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [open]);

  // Debounced lookup against the real search endpoint.
  useEffect(() => {
    const q = debouncedQuery.trim();
    const id = ++reqId.current;
    if (!open || q.length < 2) {
      setResults(null);
      setError(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    api
      .search(q, 6)
      .then((res) => {
        if (reqId.current === id) setResults(res);
      })
      .catch((err: unknown) => {
        if (reqId.current !== id) return;
        setError(
          err instanceof ApiError
            ? err.message
            : "Search is unavailable right now.",
        );
      })
      .finally(() => {
        if (reqId.current === id) setLoading(false);
      });
  }, [open, debouncedQuery]);

  const trimmed = query.trim();
  const lower = trimmed.toLowerCase();

  const items = useMemo<PaletteItem[]>(() => {
    const go = (to: string) => () => {
      onClose();
      navigate(to);
    };
    const out: PaletteItem[] = [];

    if (trimmed.length >= 2) {
      out.push({
        id: "search-all",
        group: "Search",
        label: `Search all opportunities for “${trimmed}”`,
        hint: "Explorer",
        run: go(`/opportunities?q=${encodeURIComponent(trimmed)}`),
      });
    }

    for (const cmd of NAV_COMMANDS) {
      if (lower && !cmd.label.toLowerCase().includes(lower)) continue;
      out.push({
        id: `nav:${cmd.to}`,
        group: "Go to",
        label: cmd.label,
        hint: cmd.hint,
        run: go(cmd.to),
      });
    }

    if (results) {
      for (const job of results.opportunities) {
        out.push({
          id: `job:${job.id}`,
          group: "Opportunities",
          label: job.title,
          hint: `${job.company} · ${job.source_info.name}`,
          run: go(`/opportunities/${job.slug}`),
        });
      }
      for (const cat of results.categories) {
        out.push({
          id: `cat:${cat.tag}`,
          group: "Categories",
          label: cat.tag,
          hint: `${cat.count.toLocaleString()} listings`,
          run: go(`/opportunities?category=${encodeURIComponent(cat.tag)}`),
        });
      }
      for (const src of results.sources) {
        out.push({
          id: `src:${src.id}`,
          group: "Sources",
          label: src.name,
          hint: `${src.job_count.toLocaleString()} listings`,
          run: go(`/opportunities?source=${encodeURIComponent(src.id)}`),
        });
      }
      for (const sug of results.suggestions) {
        const param =
          sug.kind === "category"
            ? "category"
            : sug.kind === "source"
              ? "source"
              : sug.kind === "tag"
                ? "tag"
                : "q";
        out.push({
          id: `sug:${sug.kind}:${sug.label}`,
          group: "Suggestions",
          label: sug.label,
          hint:
            sug.count != null
              ? `${sug.kind} · ${sug.count.toLocaleString()}`
              : sug.kind,
          run: go(`/opportunities?${param}=${encodeURIComponent(sug.label)}`),
        });
      }
    }
    return out;
  }, [trimmed, lower, results, navigate, onClose]);

  // Selection follows the list, never overshoots it.
  useEffect(() => {
    setActiveIndex((i) => (i >= items.length ? 0 : i));
  }, [items.length]);

  // Keep the highlighted row visible while arrowing through.
  useEffect(() => {
    if (!open || items.length === 0) return;
    const el = listRef.current?.querySelector<HTMLElement>(
      `[data-index="${activeIndex}"]`,
    );
    el?.scrollIntoView?.({ block: "nearest" });
  }, [activeIndex, items.length, open]);

  if (!open) return null;

  const showEmpty =
    trimmed.length >= 2 &&
    !loading &&
    !error &&
    results != null &&
    results.opportunities.length === 0 &&
    results.categories.length === 0 &&
    results.sources.length === 0 &&
    results.suggestions.length === 0;

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (items.length > 0) {
        setActiveIndex((i) => (i + 1) % items.length);
      }
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      if (items.length > 0) {
        setActiveIndex((i) => (i - 1 + items.length) % items.length);
      }
    } else if (event.key === "Enter") {
      event.preventDefault();
      items[activeIndex]?.run();
    }
  };

  let lastGroup = "";

  return (
    <div
      className="cp__backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={panelRef as React.RefObject<HTMLDivElement>}
        className="cp__panel"
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
      >
        <div className="cp__input-row">
          <svg
            className="cp__icon"
            width="17"
            height="17"
            viewBox="0 0 16 16"
            fill="none"
            aria-hidden="true"
          >
            <circle cx="7" cy="7" r="5" stroke="currentColor" strokeWidth="1.5" />
            <path
              d="m11 11 3 3"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
            />
          </svg>
          <input
            ref={inputRef}
            className="cp__input"
            type="text"
            role="combobox"
            aria-expanded="true"
            aria-controls="cp-list"
            aria-activedescendant={
              items[activeIndex] ? `cp-item-${activeIndex}` : undefined
            }
            aria-label="Search opportunities and commands"
            placeholder="Search opportunities or jump to…"
            autoComplete="off"
            spellCheck={false}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
          />
          <kbd className="cp__esc">esc</kbd>
        </div>

        <div className="cp__status" aria-live="polite">
          {loading && <span>Searching the index…</span>}
          {error && <span className="cp__error">{error}</span>}
          {showEmpty && (
            <span>
              Nothing matched “{trimmed}”. The index only contains listings the
              discovery engine has actually collected.
            </span>
          )}
          {!loading && !error && !showEmpty && trimmed.length >= 2 && results && (
            <span className="tertiary">
              {results.total.toLocaleString()} matching{" "}
              {results.total === 1 ? "opportunity" : "opportunities"} · showing{" "}
              {results.opportunities.length}
            </span>
          )}
          {!loading && trimmed.length < 2 && (
            <span className="tertiary">
              Type at least 2 characters to search the live index.
            </span>
          )}
        </div>

        <div
          id="cp-list"
          ref={listRef}
          className="cp__list"
          role="listbox"
          aria-label="Results"
        >
          {items.map((item, index) => {
            const header = item.group !== lastGroup ? item.group : null;
            lastGroup = item.group;
            const active = index === activeIndex;
            return (
              <div key={item.id} className="cp__row">
                {header && (
                  <div className="cp__group" role="presentation">
                    {header}
                  </div>
                )}
                <button
                  type="button"
                  id={`cp-item-${index}`}
                  data-index={index}
                  role="option"
                  aria-selected={active}
                  className={`cp__item${active ? " cp__item--active" : ""}`}
                  onMouseMove={() => setActiveIndex(index)}
                  onClick={item.run}
                >
                  <span className="cp__item-label">{item.label}</span>
                  {item.hint && (
                    <span className="cp__item-hint">{item.hint}</span>
                  )}
                </button>
              </div>
            );
          })}
          {items.length === 0 && trimmed.length >= 2 && !loading && !error && (
            <div className="cp__empty">No commands or results.</div>
          )}
        </div>

        <footer className="cp__footer">
          <span>↑↓ navigate</span>
          <span>↵ open</span>
          <span>esc close</span>
        </footer>
      </div>
    </div>
  );
}

