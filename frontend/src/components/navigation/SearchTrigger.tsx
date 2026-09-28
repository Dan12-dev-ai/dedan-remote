/**
 * SearchTrigger — the ⌘K affordance.
 *
 * Replaces two near-identical components (`SearchTrigger` and
 * `CommandPaletteTrigger`) that both existed to open the same palette and were
 * rendered side by side in the desktop navigation, giving users two adjacent
 * buttons for one action.
 *
 * Shows the keyboard shortcut inline rather than hiding it in a tooltip, since
 * discovering ⌘K is the whole point of the control.
 */
import { useEffect, useState } from "react";
import { NavAction } from "./NavAction";
import { SearchIcon } from "./icons";

interface SearchTriggerProps {
  onOpenPalette: () => void;
}

/** Detects a Mac so the shortcut reads ⌘K rather than Ctrl K. */
function useIsApple() {
  const [isApple, setIsApple] = useState(false);
  useEffect(() => {
    const platform = navigator.platform || navigator.userAgent;
    setIsApple(/Mac|iPhone|iPad|iPod/.test(platform));
  }, []);
  return isApple;
}

export function SearchTrigger({ onOpenPalette }: SearchTriggerProps) {
  const isApple = useIsApple();

  return (
    <button
      type="button"
      className="nav-search"
      onClick={onOpenPalette}
      aria-label="Search opportunities"
      aria-haspopup="dialog"
      title="Search opportunities"
    >
      <SearchIcon />
      <span className="nav-search__label">
        <span className="nav-search__text">Search</span>
        <kbd className="nav-search__kbd">{isApple ? "⌘" : "Ctrl"} K</kbd>
      </span>
    </button>
  );
}

export { NavAction };
