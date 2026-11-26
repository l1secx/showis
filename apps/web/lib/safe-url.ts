/**
 * Kullanıcı/API kaynaklı bağlantılar için şema doğrulaması.
 *
 * `data:`, `file:`, `javascript:` ve protokolsüz değerler bağlantı olarak
 * kullanılmaz. Harici kaynak URL'leri için yalnız http(s) kabul edilir;
 * uygulama içi göreli bağlantılar `safeLinkHref` ile korunur.
 */

const HTTP_PREFIX = /^https?:\/\//i;
const SAFE_LINK_PREFIX = /^(?:https?:|mailto:|tel:)/i;

/** Harici içerik URL'si: yalnız mutlak http(s). Aksi halde `null`. */
export function externalUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const raw = value.trim();
  if (!raw || !HTTP_PREFIX.test(raw)) return null;
  try {
    const parsed = new URL(raw);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
    return parsed.toString();
  } catch {
    return null;
  }
}

/**
 * Markdown/uygulama bağlantısı: http(s), mailto, tel, kök-göreli ve çapa
 * kabul edilir; `data:`/`file:`/`javascript:` reddedilir.
 */
export function safeLinkHref(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const raw = value.trim();
  if (!raw) return null;
  if (raw.startsWith("/") || raw.startsWith("#") || raw.startsWith("?")) return raw;
  if (!SAFE_LINK_PREFIX.test(raw)) return null;
  if (/^https?:/i.test(raw)) return externalUrl(raw);
  return raw;
}
