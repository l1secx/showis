/* OTOMATİK ÜRETİLDİ — ELLE DÜZENLEMEYİN.
 *
 * Kaynak: packages/contracts/showis_contracts (Pydantic modelleri + enums.py)
 * Üretim: `make schemas`  →  packages/contracts/scripts/export_schemas.py
 * El yazımı ek tipler: ./types.ts (bu dosyayı `export *` ile yeniden dışa aktarır)
 */

// --- Enum birlikleri (enums.py) ---
export type RiskTier = "A" | "B" | "C";
export type Platform =
  | "github"
  | "gitlab"
  | "keybase"
  | "reddit"
  | "tryhackme"
  | "hackthebox"
  | "steam"
  | "gravatar"
  | "hackernews"
  | "npm"
  | "pypi"
  | "dockerhub"
  | "devto"
  | "chess"
  | "leetcode"
  | "codeforces"
  | "stackoverflow"
  | "openalex"
  | "bitbucket"
  | "codeberg"
  | "codepen"
  | "kaggle"
  | "codewars"
  | "root-me"
  | "hackerrank"
  | "duolingo"
  | "instagram"
  | "tiktok"
  | "youtube"
  | "medium"
  | "substack"
  | "deviantart"
  | "behance"
  | "dribbble"
  | "pinterest"
  | "tumblr"
  | "about.me"
  | "linktr.ee"
  | "myanimelist"
  | "letterboxd"
  | "goodreads"
  | "lastfm"
  | "soundcloud"
  | "itch.io"
  | "telegram"
  | "vk"
  | "lichess"
  | "hashnode"
  | "wordpress"
  | "bluesky"
  | "huggingface"
  | "kitsu"
  | "wattpad"
  | "flickr"
  | "musicbrainz"
  | "mastodon"
  | "pixelfed"
  | "rubygems"
  | "nuget"
  | "packagist"
  | "9gag"
  | "vs-marketplace"
  | "speakerdeck"
  | "replit"
  | "devpost"
  | "patreon"
  | "vimeo"
  | "bandcamp"
  | "dailymotion"
  | "scratch"
  | "twitch"
  | "kick"
  | "ko-fi"
  | "buymeacoffee"
  | "gumroad"
  | "etsy"
  | "ebay"
  | "trello"
  | "slideshare"
  | "scribd"
  | "mixcloud"
  | "trakt"
  | "anilist"
  | "backloggd"
  | "producthunt"
  | "artstation"
  | "glitch"
  | "sourcehut"
  | "ao3"
  | "fandom"
  | "500px"
  | "velog"
  | "hackerone"
  | "bugcrowd"
  | "fiverr"
  | "beacons.ai"
  | "carrd.co"
  | "codesandbox"
  | "stackblitz"
  | "web"
  | "unknown";
export type ObservationKind = "search_result" | "profile" | "repository" | "commit" | "event" | "snippet" | "blocked";
export type FindingStatus = "lead" | "candidate" | "confirmed" | "rejected";
export type EntityType = "person" | "account" | "domain" | "email" | "username";
export type EdgeType = "has_account" | "cross_link" | "same_person" | "mentions" | "owned_domain";

// --- Modeller (Pydantic) ---
export interface Evidence {
  id: string;
  source_url: string;
  kind: string;
  content_hash: string;
  storage_key?: string | null;
  fetched_at?: string;
  http_status?: number | null;
  user_agent?: string | null;
  truncated?: boolean;
  notes?: string | null;
}
export interface Observation {
  id: string;
  connector: string;
  platform: Platform;
  kind: ObservationKind;
  subject: string;
  url?: string | null;
  tier: RiskTier;
  data?: Record<string, unknown>;
  evidence?: Evidence[];
  collected_at?: string;
  error?: string | null;
  blocked?: boolean;
}
export interface Entity {
  id: string;
  type: EntityType;
  platform: Platform;
  label: string;
  url?: string | null;
  attributes?: Record<string, unknown>;
  evidence_ids?: string[];
  first_seen?: string;
}
export interface Edge {
  id: string;
  type: EdgeType;
  source_entity_id: string;
  target_entity_id: string;
  confidence?: number;
  reason?: string;
  evidence_ids?: string[];
}
export interface ConfidenceBreakdown {
  username_exact_match?: number;
  handle_exact?: number;
  name_similarity?: number;
  cross_link?: number;
  bio_location_company?: number;
  commit_email_or_pgp?: number;
  avatar_exact_hash?: number;
  contradiction?: number;
  total?: number;
}
export interface Finding {
  id: string;
  case_id: string;
  scan_id: string;
  entity_id: string;
  platform: Platform;
  url?: string | null;
  label: string;
  status?: FindingStatus;
  score?: number;
  breakdown?: ConfidenceBreakdown;
  explanation?: string;
  evidence_ids?: string[];
  verified_by?: string | null;
  verified_at?: string | null;
  authenticity?: number | null;
  authenticity_reasons?: string[];
}
export interface TaskSpec {
  id: string;
  connector: string;
  platform: Platform;
  kind: string;
  params?: Record<string, unknown>;
  priority?: number;
  max_attempts?: number;
  rate_limit_key?: string | null;
}
export interface Target {
  kind?: "name" | "handle";
  first_name?: string;
  last_name?: string;
  nickname?: string;
  name_forms?: string[];
  hints?: Record<string, unknown>;
  review_required?: boolean;
}
export interface Seed {
  platform: Platform;
  url?: string | null;
  username?: string | null;
  email?: string | null;
  source?: string;
}
export interface Budget {
  max_tasks?: number;
  max_http_requests?: number;
  max_llm_calls?: number;
  max_wall_seconds?: number;
  max_cost_usd?: number;
}
export interface ScanContext {
  case_id: string;
  scan_id: string;
  target: Target;
  seeds?: Seed[];
  budget?: Budget;
  llm_session_id?: string;
}
export interface GithubProfile {
  login: string;
  name?: string | null;
  bio?: string | null;
  company?: string | null;
  location?: string | null;
  blog?: string | null;
  twitter_username?: string | null;
  public_email?: string | null;
  avatar_url?: string | null;
  avatar_sha256?: string | null;
  followers?: number | null;
  following?: number | null;
  public_repos?: number | null;
  created_at?: string | null;
  updated_at?: string | null;
  repo_names?: string[];
  languages?: string[];
  social_accounts?: Record<string, string>;
  commit_emails?: string[];
  gpg_keys?: string[];
}
export interface ThmProfile {
  username: string;
  display_name?: string | null;
  rank?: number | null;
  streak?: number | null;
  rooms_completed?: number | null;
  badges?: string[];
  avatar_url?: string | null;
  avatar_sha256?: string | null;
  links?: string[];
  github_link?: string | null;
  twitter_link?: string | null;
  telegram_link?: string | null;
}
export interface HtbProfile {
  username?: string | null;
  profile_id?: string | null;
  display_name?: string | null;
  rank?: string | null;
  machines_completed?: number | null;
  country?: string | null;
  avatar_url?: string | null;
  avatar_sha256?: string | null;
  links?: string[];
  github_link?: string | null;
  twitter_link?: string | null;
  telegram_link?: string | null;
}
export interface WebProfile {
  url: string;
  title?: string | null;
  snippet?: string | null;
  extracted_usernames?: string[];
  extracted_names?: string[];
  links?: string[];
}
export interface SearchQuery {
  query: string;
  platform: Platform;
  rationale?: string;
  priority?: number;
}
export interface QueryPlan {
  queries?: SearchQuery[];
}
export interface UsernameVariant {
  username: string;
  pattern: string;
  confidence?: number;
}
export interface VariantSet {
  variants?: UsernameVariant[];
  name_forms?: string[];
}
export interface ExtractionResult {
  display_name?: string | null;
  username?: string | null;
  bio?: string | null;
  location?: string | null;
  company?: string | null;
  links?: string[];
  emails?: string[];
  signals?: string[];
}
