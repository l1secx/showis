/**
 * Seçim ve klavye odağı yardımcıları. Saf fonksiyonlar; DOM veya canvas
 * gerektirmez. Motor çoklu seçimi bu küme işlemleriyle yönetir.
 */

// @ts-ignore -- Node `--test` ESM için .ts uzantısı zorunlu; apps/web/tsconfig.json allowImportingTsExtensions olmadan reddediyor.
import type { GEdge } from "./types.ts";

/** Tıklama davranışı: değiştir, ekle veya aç/kapat. */
export type SelectMode = "replace" | "add" | "toggle";

/**
 * Mevcut seçime tek bir kimlik uygular. `null` her zaman seçimi temizler.
 * `replace` yalnız verilen düğümü bırakır; `toggle` varsa çıkarır, yoksa ekler.
 */
export function applySelection(
  current: ReadonlySet<string>,
  id: string | null,
  mode: SelectMode
): Set<string> {
  if (id === null) return new Set();
  if (mode === "replace") return new Set([id]);
  const next = new Set(current);
  if (mode === "add") {
    next.add(id);
  } else if (next.has(id)) {
    next.delete(id);
  } else {
    next.add(id);
  }
  return next;
}

/**
 * Küme sırasını korur, birincil kimliği sona taşır. Birincil yoksa sıra
 * olduğu gibi döner.
 */
export function selectionArray(
  current: ReadonlySet<string>,
  primary: string | null
): string[] {
  const ids = [...current];
  if (primary === null || !current.has(primary)) return ids;
  return [...ids.filter((id) => id !== primary), primary];
}

/** Bir düğümün komşu kimlikleri (kendisi hariç, tekrarsız). */
export function neighborIds(id: string, edges: readonly Pick<GEdge, "source" | "target">[]): string[] {
  const neighbors = new Set<string>();
  for (const edge of edges) {
    if (edge.source === id) neighbors.add(edge.target);
    else if (edge.target === id) neighbors.add(edge.source);
  }
  neighbors.delete(id);
  return [...neighbors];
}

export interface ConnectedNode {
  id: string;
  label: string;
  degree: number;
}

/**
 * Derecesi en yüksek düğümler. Eşitlikte etiket, sonra kimlik sıralanır;
 * çıktı deterministiktir. Erişilebilir özet için kullanılır.
 */
export function topConnectedNodes(
  nodes: readonly { id: string; label: string }[],
  edges: readonly Pick<GEdge, "source" | "target">[],
  limit = 10
): ConnectedNode[] {
  const degree = new Map<string, number>();
  for (const node of nodes) degree.set(node.id, 0);
  for (const edge of edges) {
    if (edge.source === edge.target) {
      if (degree.has(edge.source)) degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1);
      continue;
    }
    if (degree.has(edge.source)) degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1);
    if (degree.has(edge.target)) degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1);
  }
  const safeLimit = Number.isFinite(limit) ? Math.max(0, Math.floor(limit)) : 0;
  return nodes
    .map((node) => ({
      id: node.id,
      label: node.label,
      degree: degree.get(node.id) ?? 0,
    }))
    .sort(
      (a, b) =>
        b.degree - a.degree ||
        compareLabels(a.label, b.label) ||
        a.id.localeCompare(b.id)
    )
    .slice(0, safeLimit);
}

/**
 * Etiket sırası: Türkçe, büyük/küçük harf duyarsız. Klavye gezinmesi ve
 * erişilebilir özet aynı sırayı kullanır.
 */
export function compareLabels(a: string, b: string): number {
  return a.localeCompare(b, "tr", { sensitivity: "base" });
}

/**
 * Klavye sırası: etikete, eşitlikte kimliğe göre. Görünür düğümler her
 * Tab'da bu sırayla gezilir.
 */
export function sortForKeyboard<T extends { id: string; label: string }>(
  nodes: readonly T[]
): T[] {
  return nodes
    .slice()
    .sort(
      (a, b) => compareLabels(a.label, b.label) || a.id.localeCompare(b.id)
    );
}

/**
 * Tab yönünde sonraki odak indeksi. `null`, odağın canvas dışına çıkması
 * gerektiğini bildirir (klavye tuzağı olmaz). İlk Tab ilk düğüme, ilk
 * Shift+Tab son düğüme gider.
 */
export function nextFocusIndex(
  index: number,
  direction: 1 | -1,
  count: number
): number | null {
  if (!Number.isFinite(count) || count <= 0) return null;
  if (!Number.isFinite(index) || index < 0 || index >= count) {
    return direction > 0 ? 0 : count - 1;
  }
  const next = index + direction;
  if (next < 0 || next >= count) return null;
  return next;
}
