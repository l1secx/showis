/**
 * Platform etiketleri ve renkleri — UI genelinde tek kaynak.
 *
 * `packages/contracts/showis_contracts/enums.py::Platform` değerleriyle
 * hizalıdır; bilinmeyen/yeni bir platform gelirse `platformMeta()` güvenli
 * bir fallback döndürür (ham değer + nötr renk), böylece UI kırılmaz.
 *
 * `color`: düğüm dolgusu (grafik) — marka rengi.
 * `accent`: koyu zeminde okunabilir vurgu (rozet noktası/kenarlık) — çok koyu
 *           marka renkleri (github, tiktok, medium…) için açık ton.
 */

import type { Platform } from "../../../lib/types";

export interface PlatformMeta {
  label: string;
  color: string;
  accent: string;
}

const NEUTRAL: PlatformMeta = { label: "bilinmeyen", color: "#7f8c8d", accent: "#94a3b8" };

const PLATFORM_META_DEFINED = {
  // --- Derin connector'lar ---
  github: { label: "GitHub", color: "#24292f", accent: "#a9b8cb" },
  gitlab: { label: "GitLab", color: "#fc6d26", accent: "#fc6d26" },
  keybase: { label: "Keybase", color: "#33a0ff", accent: "#33a0ff" },
  reddit: { label: "Reddit", color: "#ff4500", accent: "#ff4500" },
  tryhackme: { label: "TryHackMe", color: "#e1306c", accent: "#e1306c" },
  hackthebox: { label: "Hack The Box", color: "#9b59b6", accent: "#c084fc" },
  steam: { label: "Steam", color: "#1b2838", accent: "#66c0f4" },
  gravatar: { label: "Gravatar", color: "#1d4ed8", accent: "#60a5fa" },
  hackernews: { label: "Hacker News", color: "#ff6600", accent: "#ff8c42" },
  npm: { label: "npm", color: "#cb3837", accent: "#f87171" },
  pypi: { label: "PyPI", color: "#3776ab", accent: "#60a5fa" },
  dockerhub: { label: "Docker Hub", color: "#2496ed", accent: "#2496ed" },
  devto: { label: "DEV", color: "#0a0a0a", accent: "#d1d5db" },
  chess: { label: "Chess.com", color: "#81b64c", accent: "#a3e635" },
  leetcode: { label: "LeetCode", color: "#ffa116", accent: "#ffb84d" },
  codeforces: { label: "Codeforces", color: "#1f8acb", accent: "#60a5fa" },
  // T-308: anahtarsız public API connector'ları
  stackoverflow: { label: "Stack Overflow", color: "#f48024", accent: "#f9a15b" },
  openalex: { label: "OpenAlex", color: "#1f6feb", accent: "#60a5fa" },

  // --- Site-check / profil platformları ---
  bitbucket: { label: "Bitbucket", color: "#2684ff", accent: "#60a5fa" },
  codeberg: { label: "Codeberg", color: "#2188ff", accent: "#60a5fa" },
  codepen: { label: "CodePen", color: "#1e1f26", accent: "#d1d5db" },
  kaggle: { label: "Kaggle", color: "#20beff", accent: "#20beff" },
  codewars: { label: "Codewars", color: "#b1361e", accent: "#f87171" },
  "root-me": { label: "Root-Me", color: "#3f4f66", accent: "#a9b8cb" },
  hackerrank: { label: "HackerRank", color: "#2ec866", accent: "#34d399" },
  duolingo: { label: "Duolingo", color: "#58cc02", accent: "#84cc16" },
  instagram: { label: "Instagram", color: "#c13584", accent: "#e879f9" },
  tiktok: { label: "TikTok", color: "#010101", accent: "#d1d5db" },
  youtube: { label: "YouTube", color: "#ff0000", accent: "#ef4444" },
  medium: { label: "Medium", color: "#000000", accent: "#d1d5db" },
  substack: { label: "Substack", color: "#ff6719", accent: "#fb923c" },
  deviantart: { label: "DeviantArt", color: "#05cc47", accent: "#34d399" },
  behance: { label: "Behance", color: "#1769ff", accent: "#60a5fa" },
  dribbble: { label: "Dribbble", color: "#ea4c89", accent: "#f472b6" },
  pinterest: { label: "Pinterest", color: "#e60023", accent: "#f87171" },
  tumblr: { label: "Tumblr", color: "#35465c", accent: "#94a3b8" },
  "about.me": { label: "About.me", color: "#004b6b", accent: "#38bdf8" },
  "linktr.ee": { label: "Linktree", color: "#43e660", accent: "#4ade80" },
  myanimelist: { label: "MyAnimeList", color: "#2e51a2", accent: "#818cf8" },
  letterboxd: { label: "Letterboxd", color: "#00e054", accent: "#4ade80" },
  goodreads: { label: "Goodreads", color: "#372213", accent: "#a8a29e" },
  lastfm: { label: "Last.fm", color: "#b90000", accent: "#f87171" },
  soundcloud: { label: "SoundCloud", color: "#ff5500", accent: "#fb923c" },
  "itch.io": { label: "itch.io", color: "#fa5c5c", accent: "#f87171" },
  telegram: { label: "Telegram", color: "#229ed9", accent: "#38bdf8" },
  vk: { label: "VK", color: "#0077ff", accent: "#60a5fa" },
  lichess: { label: "Lichess", color: "#b58863", accent: "#d6b48a" },
  hashnode: { label: "Hashnode", color: "#2962ff", accent: "#60a5fa" },
  wordpress: { label: "WordPress", color: "#21759b", accent: "#38bdf8" },
  bluesky: { label: "Bluesky", color: "#0285ff", accent: "#60a5fa" },
  huggingface: { label: "Hugging Face", color: "#ffd21e", accent: "#facc15" },
  kitsu: { label: "Kitsu", color: "#f75239", accent: "#fb923c" },
  wattpad: { label: "Wattpad", color: "#ff6122", accent: "#fb923c" },
  flickr: { label: "Flickr", color: "#0063dc", accent: "#60a5fa" },
  musicbrainz: { label: "MusicBrainz", color: "#ba478f", accent: "#e879f9" },
  mastodon: { label: "Mastodon", color: "#6364ff", accent: "#a5b4fc" },
  pixelfed: { label: "Pixelfed", color: "#3d6ad1", accent: "#60a5fa" },
  rubygems: { label: "RubyGems", color: "#e9573f", accent: "#fb923c" },
  nuget: { label: "NuGet", color: "#004880", accent: "#38bdf8" },
  packagist: { label: "Packagist", color: "#f28d1a", accent: "#fb923c" },
  "9gag": { label: "9GAG", color: "#000000", accent: "#d1d5db" },
  "vs-marketplace": { label: "VS Marketplace", color: "#007acc", accent: "#38bdf8" },
  speakerdeck: { label: "Speaker Deck", color: "#009287", accent: "#2dd4bf" },
  replit: { label: "Replit", color: "#f26207", accent: "#fb923c" },
  devpost: { label: "Devpost", color: "#003e54", accent: "#38bdf8" },
  patreon: { label: "Patreon", color: "#f96854", accent: "#fb923c" },
  vimeo: { label: "Vimeo", color: "#1ab7ea", accent: "#22d3ee" },
  bandcamp: { label: "Bandcamp", color: "#629aa9", accent: "#7dd3fc" },
  dailymotion: { label: "Dailymotion", color: "#0066dc", accent: "#60a5fa" },
  scratch: { label: "Scratch", color: "#ffab19", accent: "#fbbf24" },

  // --- Genel ---
  web: { label: "web", color: "#3498db", accent: "#38bdf8" },
  unknown: NEUTRAL,
} satisfies Partial<Record<Platform, PlatformMeta>>;

export const PLATFORM_META: Record<string, PlatformMeta> = PLATFORM_META_DEFINED;

/** Bilinmeyen platformda güvenli fallback: ham değer + nötr renk. */
export function platformMeta(platform: string | null | undefined): PlatformMeta {
  const key = String(platform ?? "").trim();
  if (!key) return NEUTRAL;
  const known = PLATFORM_META[key.toLowerCase()];
  if (known) return known;
  return { label: key, color: NEUTRAL.color, accent: NEUTRAL.accent };
}
