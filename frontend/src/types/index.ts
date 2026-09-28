/** Types mirroring the DEDAN Remote FastAPI schemas (api/schemas.py). */

export interface ScoreDimension {
  score: number;
  weight: number;
  contribution: number;
}

export interface MatchReason {
  dimension: string;
  label: string;
  detail: string;
}

export interface ScoreExplanation {
  basis: "system_ranking";
  label: string;
  total: number;
  dimensions: Record<string, ScoreDimension>;
  reasons: MatchReason[];
}

export interface PreferenceMatch {
  basis: "user_preferences";
  label: string;
  score: number;
  reasons: string[];
  note: string;
}

export interface Freshness {
  label: string;
  discovered_at: string | null;
  posted_date: string | null;
  age_days: number | null;
  is_stale: boolean;
}

export interface SourceInfo {
  id: string;
  name: string;
  monitored: boolean;
  homepage: string | null;
}

export interface JobSummary {
  id: string;
  slug: string;
  title: string;
  company: string;
  source: string;
  source_info: SourceInfo;
  url: string;
  apply_url: string | null;
  apply_host: string | null;
  salary: string | null;
  salary_disclosed: boolean;
  country: string | null;
  location_label: string;
  remote: boolean;
  posted_date: string | null;
  description: string | null;
  tags: string[];
  category: string | null;
  is_ai_related: boolean;
  experience_hint: string | null;
  discovered_at: string | null;
  freshness: Freshness;
  score: number;
  score_explanation: ScoreExplanation | null;
  preference_match: PreferenceMatch | null;
}

export interface JobDetail extends JobSummary {
  intelligence: Record<string, unknown> | null;
  intelligence_note: string | null;
  source_checked_at: string | null;
}

export interface Page {
  items: JobSummary[];
  page: number;
  page_size: number;
  total: number;
  pages: number;
  has_next: boolean;
  has_prev: boolean;
}

export interface SourceStat {
  id: string;
  name: string;
  job_count: number;
  monitored: boolean;
  last_success: string | null;
  circuit_open: boolean;
}

export interface CategoryStat {
  tag: string;
  count: number;
}

export interface StatsResponse {
  total_opportunities: number;
  new_last_7_days: number;
  sources_monitored: number;
  discovery_interval_minutes: number;
  last_discovery_at: string | null;
  last_cycle_status: string | null;
  total_cycles: number;
}

export interface StatusResponse {
  engine: "operational" | "degraded" | "idle";
  last_discovery_at: string | null;
  last_discovery_label: string;
  last_cycle_status: string | null;
  sources_monitored: number;
  sources_failing: number;
  total_cycles: number;
  note: string | null;
}

export type ApplicationStatus =
  | "viewed"
  | "saved"
  | "application_started"
  | "applied"
  | "rejected"
  | "interview"
  | "offer";

export interface ApplicationOut {
  id: string;
  job: JobSummary;
  status: ApplicationStatus;
  note: string | null;
  created_at: string;
  updated_at: string;
}

export interface SavedItem {
  job: JobSummary;
  note: string | null;
  saved_at: string;
}

export interface User {
  id: string;
  email: string;
  display_name: string | null;
  created_at: string;
}

export interface Preferences {
  categories: string[];
  experience: "beginner" | "intermediate" | "advanced" | null;
  regions: string[];
  updated_at: string | null;
}

export interface ProfileOut {
  user: User;
  preferences: Preferences;
}

export interface RecommendationPage {
  items: JobSummary[];
  basis: string;
  method: string;
  preferences: Preferences;
}

export interface ApiErrorBody {
  error: { code: string; message: string };
}

export interface AssistantResponse {
  response: string;
}

export interface JobFilters {
  q?: string;
  source?: string;
  category?: string;
  tag?: string;
  country?: string;
  worldwide?: boolean;
  remote?: boolean;
  min_score?: number;
  max_age_days?: number;
  has_salary?: boolean;
  beginner?: boolean;
  ai?: boolean;
  sort?: "newest" | "best_match" | "score" | "salary" | "freshness";
  page?: number;
  page_size?: number;
}

export interface SearchSuggestion {
  label: string;
  kind: "tag" | "source" | "category" | "keyword";
  count?: number | null;
}

export interface SearchResponse {
  query: string;
  total: number;
  opportunities: JobSummary[];
  categories: CategoryStat[];
  sources: SourceStat[];
  suggestions: SearchSuggestion[];
  note: string;
}

export interface NotificationOut {
  id: string;
  kind: "saved" | "application" | "discovery";
  title: string;
  detail: string;
  at: string;
  job?: JobSummary | null;
}

export interface Message {
  id: string;
  type: "user" | "assistant";
  content: string;
  timestamp: string;
}

export interface NotificationsResponse {
  items: NotificationOut[];
  note: string;
}

