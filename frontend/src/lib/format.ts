/**
 * Presentation helpers for DEDAN Remote.
 *
 * Integrity rules encoded here:
 *  - A missing value renders as explicit absence ("Not disclosed",
 *    "Not available"), never as a zero, a guess or a placeholder.
 *  - Nothing is described as "closing soon", "trending" or "popular" because
 *    the backend stores no data that could support those claims.
 *  - Qualitative wording is always derived from a real stored number, and the
 *    banding thresholds are declared in one place so the mapping is auditable.
 */

import type {
  ApplicationStatus,
  Freshness,
  JobSummary,
  ScoreDimension,
} from "../types";

/* ── Absence ─────────────────────────────────────────────────────────────── */

export const NOT_DISCLOSED = "Not disclosed";
export const NOT_AVAILABLE = "Not available";

/* ── Time ────────────────────────────────────────────────────────────────── */

const MINUTE = 60;
const HOUR = MINUTE * 60;
const DAY = HOUR * 24;
const WEEK = DAY * 7;
const MONTH = DAY * 30;

/**
 * Compact relative time ("2h ago"). Returns null for an absent or unparsable
 * timestamp so callers choose their own fallback wording rather than printing
 * "Invalid Date".
 */
export function relativeTime(iso?: string | null): string | null {
  if (!iso) return null;
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return null;

  const seconds = Math.floor((Date.now() - then.getTime()) / 1000);
  if (seconds < 0) return "scheduled";
  if (seconds < 60) return "just now";
  if (seconds < HOUR) return `${Math.floor(seconds / MINUTE)}m ago`;
  if (seconds < DAY) return `${Math.floor(seconds / HOUR)}h ago`;
  if (seconds < WEEK) return `${Math.floor(seconds / DAY)}d ago`;
  if (seconds < MONTH) return `${Math.floor(seconds / WEEK)}w ago`;
  if (seconds < DAY * 365) return `${Math.floor(seconds / MONTH)}mo ago`;
  return `${Math.floor(seconds / (DAY * 365))}y ago`;
}

/** Absolute date for tooltips and detail views. Locale-aware. */
export function absoluteDate(iso?: string | null): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function absoluteDateTime(iso?: string | null): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** "Good morning" / "Good afternoon" / "Good evening" from the local clock. */
export function greeting(date = new Date()): string {
  const hour = date.getHours();
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

/* ── Language ────────────────────────────────────────────────────────────── */

export function plural(count: number, singular: string, pluralForm?: string): string {
  return count === 1 ? singular : (pluralForm ?? `${singular}s`);
}

export function countWithNoun(count: number, noun: string): string {
  return `${count.toLocaleString()} ${plural(count, noun)}`;
}

export function titleCase(value: string): string {
  return value
    .split(/\s+/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

/** Human label for a canonical category slug. Mirrors the API taxonomy. */
const CATEGORY_LABELS: Record<string, string> = {
  "ai-ml": "AI / ML",
  "software": "Software engineering",
  "data": "Data & annotation",
  "ai-training": "AI training",
  "research": "Research",
  "general-remote": "Remote digital work",
};

export function categoryLabel(slug?: string | null): string | null {
  if (!slug) return null;
  return CATEGORY_LABELS[slug] ?? titleCase(slug.replace(/-/g, " "));
}

/** The canonical category slugs, in editorial order. */
export const CATEGORY_SLUGS: readonly string[] = Object.keys(CATEGORY_LABELS);

/** One-line sense of what each category contains. */
export const CATEGORY_BLURBS: Record<string, string> = {
  "ai-ml": "Model training, evaluation and applied machine learning.",
  "software": "Engineering roles across the stack, remote-first.",
  "data": "Annotation, labelling, transcription and dataset work.",
  "ai-training": "Evaluating, rating and reviewing model output.",
  "research": "Analysis and research-oriented contract work.",
  "general-remote": "Broad remote and freelance digital work.",
};

/* ── Confidence banding ──────────────────────────────────────────────────────
   Qualitative wording derived from a real 0–100 dimension score. Thresholds
   live here so the mapping is inspectable and testable.                    */

export type Band = "strong" | "high" | "moderate" | "low" | "minimal";

export type Tone = "success" | "accent" | "neutral" | "muted";

export const BAND_THRESHOLDS = {
  strong: 85,
  high: 70,
  moderate: 45,
  low: 25,
} as const;

export function bandOf(score: number): Band {
  if (score >= BAND_THRESHOLDS.strong) return "strong";
  if (score >= BAND_THRESHOLDS.high) return "high";
  if (score >= BAND_THRESHOLDS.moderate) return "moderate";
  if (score >= BAND_THRESHOLDS.low) return "low";
  return "minimal";
}

export const BAND_LABELS: Record<Band, string> = {
  strong: "Strong",
  high: "High",
  moderate: "Moderate",
  low: "Low",
  minimal: "Minimal",
};

/** Restrained tone mapping: one success hue, one accent hue, then quiet. */
export const BAND_TONES: Record<Band, Tone> = {
  strong: "success",
  high: "accent",
  moderate: "neutral",
  low: "muted",
  minimal: "muted",
};

/**
 * Display metadata for a system-ranking dimension.
 *
 * Every dimension in the discovery engine's ranking is *positively* oriented
 * (a higher score is always better). Some read better to a human when framed
 * the other way round, so `invert` flips the presented value while the
 * underlying score stays untouched — "Experience barrier: Low" is the honest
 * reading of a high beginner-accessibility score.
 */
export interface DimensionDisplay {
  label: string;
  meaning: string;
  invert?: boolean;
}

export const DIMENSION_DISPLAY: Record<string, DimensionDisplay> = {
  remote: {
    label: "Remote eligibility",
    meaning: "The listing is remote work.",
  },
  worldwide: {
    label: "Geographic openness",
    meaning: "No single-country restriction detected in the listing text.",
  },
  salary: {
    label: "Compensation listed",
    meaning: "The source listing publishes a salary or pay rate.",
  },
  beginner: {
    label: "Experience barrier",
    meaning: "How low the stated experience requirement is.",
    invert: true,
  },
  ai_related: {
    label: "AI relevance",
    meaning: "Title, tags or description reference AI or data work.",
  },
  english: {
    label: "English-language role",
    meaning: "The listing appears to be written in English.",
  },
  simplicity: {
    label: "Application barrier",
    meaning: "Low-friction signals such as flexible or online-only.",
    invert: true,
  },
  freshness: {
    label: "Freshness",
    meaning: "How recent the listing's posted date is.",
  },
};

export interface DimensionReading {
  key: string;
  label: string;
  meaning: string;
  /** 0–100, already inverted where the display calls for it. */
  score: number;
  band: Band;
  word: string;
  tone: Tone;
  /** Raw score exactly as the ranking engine stored it. */
  raw: number;
  /** Configured weight, when available. */
  weight?: number;
}

function clamp01to100(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(100, Math.max(0, value));
}

/** Turn one stored dimension into something a person can read. */
export function readDimension(
  key: string,
  dimension: ScoreDimension,
): DimensionReading {
  const display: DimensionDisplay = DIMENSION_DISPLAY[key] ?? {
    label: titleCase(key.replace(/_/g, " ")),
    meaning: "Ranking dimension reported by the discovery engine.",
  };
  const raw = clamp01to100(dimension.score);
  const score = display.invert ? 100 - raw : raw;
  const band = bandOf(score);
  return {
    key,
    label: display.label,
    meaning: display.meaning,
    score,
    band,
    word: BAND_LABELS[band],
    tone: BAND_TONES[band],
    raw,
    weight: dimension.weight,
  };
}

/** All readings for a job, most heavily weighted first. */
export function readDimensions(
  dimensions: Record<string, ScoreDimension> | null | undefined,
): DimensionReading[] {
  if (!dimensions) return [];
  return Object.entries(dimensions)
    .map(([key, dimension]) => readDimension(key, dimension))
    .sort((a, b) => (b.weight ?? 0) - (a.weight ?? 0));
}

/** Band for an overall system score. */
export function scoreBand(score: number): {
  band: Band;
  word: string;
  tone: Tone;
} {
  const band = bandOf(clamp01to100(score));
  return { band, word: BAND_LABELS[band], tone: BAND_TONES[band] };
}

/* ── Opportunity field formatting ───────────────────────────────────────── */

/** Compensation, stated honestly. */
export function compensation(
  job: Pick<JobSummary, "salary" | "salary_disclosed">,
): { value: string; disclosed: boolean } {
  if (job.salary_disclosed && job.salary) {
    return { value: job.salary, disclosed: true };
  }
  return { value: NOT_DISCLOSED, disclosed: false };
}

export const EXPERIENCE_LABELS: Record<string, string> = {
  beginner: "Entry friendly",
  intermediate: "Intermediate",
  advanced: "Senior",
};

/** Accepts the API's lowercase hints; unknown values are title-cased. */
export function experienceLabel(hint?: string | null): string | null {
  if (!hint) return null;
  return EXPERIENCE_LABELS[hint.toLowerCase()] ?? titleCase(hint);
}

/** Freshness tone: recent listings take the success hue, stale ones go quiet. */
export function freshnessTone(freshness?: Freshness | null): Tone {
  if (!freshness) return "muted";
  if (freshness.is_stale) return "muted";
  const age = freshness.age_days;
  if (age === null || age === undefined) return "neutral";
  if (age <= 3) return "success";
  if (age <= 14) return "accent";
  return "neutral";
}

/** Hostname for an external source, safe on unparsable input. */
export function hostOf(url?: string | null): string | null {
  if (!url) return null;
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return null;
  }
}

/**
 * A listing title is only worth showing as a title when it looks like one.
 * Scraped pages occasionally yield navigation text or a stray number as the
 * heading. We do not alter or hide that data — we simply label it, so the UI
 * reads as honest rather than broken.
 */
export function looksLikeTitle(title: string): boolean {
  const trimmed = title.trim();
  if (trimmed.length < 6) return false;
  if (/^\d+$/.test(trimmed)) return false;
  return /[a-z]/i.test(trimmed);
}

/** Clean, human heading for an opportunity, with an honest fallback. */
export function opportunityHeading(title: string): string {
  const trimmed = (title ?? "").trim();
  return trimmed.length > 0 ? trimmed : NOT_AVAILABLE;
}

/* ── Applications ────────────────────────────────────────────────────────── */

export interface StatusMeta {
  label: string;
  tone: Tone;
  /** Pipeline stage index, or -1 for stages outside the happy path. */
  stage: number;
}

export const APPLICATION_STATUS: Record<ApplicationStatus, StatusMeta> = {
  viewed: { label: "Reviewed", tone: "neutral", stage: -1 },
  saved: { label: "Shortlisted", tone: "accent", stage: 1 },
  application_started: { label: "Preparing", tone: "accent", stage: 2 },
  applied: { label: "Applied", tone: "accent", stage: 3 },
  interview: { label: "Interview", tone: "success", stage: 4 },
  offer: { label: "Offer", tone: "success", stage: 5 },
  rejected: { label: "Closed", tone: "muted", stage: -1 },
};

export function statusMeta(status: ApplicationStatus): StatusMeta {
  return (
    APPLICATION_STATUS[status] ?? {
      label: titleCase(String(status).replace(/_/g, " ")),
      tone: "neutral",
      stage: -1,
    }
  );
}

/** The forward pipeline the board renders, in order. */
export const PIPELINE_STAGES: readonly ApplicationStatus[] = [
  "saved",
  "application_started",
  "applied",
  "interview",
  "offer",
];

/** Every status a user can move an opportunity into from the board. */
export const BOARD_TARGETS: readonly ApplicationStatus[] = [
  "saved",
  "application_started",
  "applied",
  "interview",
  "offer",
  "rejected",
];
