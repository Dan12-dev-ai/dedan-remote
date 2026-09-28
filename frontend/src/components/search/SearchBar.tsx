import { useEffect, useRef, useState } from "react";
import "./SearchBar.css";

interface SearchBarProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  autoFocus?: boolean;
  size?: "hero" | "page";
  id?: string;
}

/**
 * Debounced search input — calls onChange immediately (parent debounces),
 * renders a trailing clear affordance, keyboard accessible.
 */
export function SearchBar({
  value,
  onChange,
  placeholder = "Search opportunities...",
  autoFocus,
  size = "page",
  id,
}: SearchBarProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [focused, setFocused] = useState(false);

  useEffect(() => {
    if (autoFocus) inputRef.current?.focus();
  }, [autoFocus]);

  return (
    <div
      className={`search search--${size}${focused ? " search--focus" : ""}`}
    >
      <svg className="search__icon" width="17" height="17" viewBox="0 0 16 16"
        fill="none" aria-hidden="true">
        <circle cx="7" cy="7" r="5" stroke="currentColor" strokeWidth="1.5" />
        <path d="m11 11 3 3" stroke="currentColor" strokeWidth="1.5"
          strokeLinecap="round" />
      </svg>
      <input
        ref={inputRef}
        id={id}
        className="search__input"
        type="search"
        value={value}
        placeholder={placeholder}
        aria-label="Search opportunities"
        autoComplete="off"
        onChange={(e) => onChange(e.target.value)}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
      />
      {value && (
        <button
          type="button"
          className="search__clear"
          aria-label="Clear search"
          onClick={() => {
            onChange("");
            inputRef.current?.focus();
          }}
        >
          ✕
        </button>
      )}
    </div>
  );
}
