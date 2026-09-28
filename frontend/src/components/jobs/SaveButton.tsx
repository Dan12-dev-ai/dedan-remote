import { useState } from "react";
import "./SaveButton.css";

interface SaveButtonProps {
  saved: boolean;
  onClick: () => void;
  title?: string;
}

/** Heart save toggle with a single, meaningful state animation. */
export function SaveButton({ saved, onClick, title }: SaveButtonProps) {
  const [burst, setBurst] = useState(false);

  const handle = () => {
    if (!saved) {
      setBurst(true);
      window.setTimeout(() => setBurst(false), 480);
    }
    onClick();
  };

  return (
    <button
      type="button"
      className={`save-btn${saved ? " save-btn--on" : ""}${
        burst ? " save-btn--burst" : ""}`}
      onClick={handle}
      aria-pressed={saved}
      aria-label={
        saved
          ? `Remove ${title ?? "opportunity"} from saved`
          : `Save ${title ?? "opportunity"}`
      }
      title={saved ? "Saved" : "Save"}
    >
      <svg width="17" height="17" viewBox="0 0 24 24"
        fill={saved ? "currentColor" : "none"}
        stroke="currentColor" strokeWidth="1.8"
        aria-hidden="true">
        <path
          d="M12 21s-7.5-4.7-10-9.3C.4 8.6 2.2 4.9 5.8 4.3c2.1-.3 4.1.6 5.2 2.3 1.1-1.7 3.1-2.6 5.2-2.3 3.6.6 5.4 4.3 3.8 7.4C19.5 16.3 12 21 12 21z"
          strokeLinejoin="round"
        />
      </svg>
    </button>
  );
}
