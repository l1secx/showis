/**
 * Sağ tık bağlam menüsünün modeli. Motor olayı üretir, menü React tarafında
 * çizilir; bu dosya yalnız hangi eylemlerin etkin olduğunu saflaştırır.
 */

// @ts-ignore -- Node `--test` ESM için .ts uzantısı zorunlu; apps/web/tsconfig.json allowImportingTsExtensions olmadan reddediyor.
import type { GEdge } from "./types.ts";

export type ContextMenuAction =
  | "expand-neighbors"
  | "only-neighbors"
  | "hide"
  | "path-from"
  | "path-to"
  | "copy-label"
  | "focus-selection"
  | "clear-selection";

export interface ContextMenuItem {
  action: ContextMenuAction;
  label: string;
  /** Kısayol ipucu; görünürdür ama zorunlu değildir. */
  hint?: string;
  disabled: boolean;
  /** Yıkıcı eylem (gizle); React tarafı `btn-danger` kullanır. */
  danger?: boolean;
}

export interface ContextMenuInput {
  /** Sağ tıklanan hedef kimliği; boş alan `null`. */
  targetId: string | null;
  /** O an seçili düğümler; hedef dahil değilse menü onu seçer. */
  selection: ReadonlySet<string>;
  /** Görünür kenarlar; komşu sayısı ve yol kuralları için. */
  edges: readonly Pick<GEdge, "source" | "target">[];
  /** Gizleme için en az bir görünür düğüm kalmalı. */
  visibleNodeCount: number;
}

/** Hedefin komşu sayısı (kendisi hariç). */
export function contextNeighborCount(
  targetId: string | null,
  edges: readonly Pick<GEdge, "source" | "target">[]
): number {
  if (!targetId) return 0;
  const neighbors = new Set<string>();
  for (const edge of edges) {
    if (edge.source === targetId) neighbors.add(edge.target);
    else if (edge.target === targetId) neighbors.add(edge.source);
  }
  neighbors.delete(targetId);
  return neighbors.size;
}

/**
 * Seçim bağlamına göre menü öğelerini üretir. Hedef boş alansa yalnız
 * "Seçimi odakla" ve "Seçimi temizle" etkin olabilir.
 */
export function buildContextMenu(input: ContextMenuInput): ContextMenuItem[] {
  const targetId = input.targetId;
  const hasSelection = input.selection.size > 0;
  const effectiveSelection = targetId
    ? input.selection.has(targetId)
      ? input.selection
      : new Set([targetId])
    : input.selection;
  const selectionSize = effectiveSelection.size;
  const neighbors = contextNeighborCount(targetId, input.edges);
  const canHide = targetId !== null && input.visibleNodeCount > selectionSize;

  return [
    {
      action: "expand-neighbors",
      label: "Komşuları genişlet",
      hint: "Gizli komşuları görünür yap",
      disabled: targetId === null || neighbors === 0,
    },
    {
      action: "only-neighbors",
      label: "Yalnız komşuları göster",
      hint: "Görünürlüğü komşulara daralt",
      disabled: targetId === null || neighbors === 0,
    },
    {
      action: "hide",
      label: "Gizle",
      hint: "Düğümü görünümden çıkar",
      disabled: !canHide,
      danger: true,
    },
    {
      action: "path-from",
      label: "Yola başlangıç",
      disabled: targetId === null || hasSelection && selectionSize > 1,
    },
    {
      action: "path-to",
      label: "Yola hedef",
      disabled: targetId === null || hasSelection && selectionSize > 1,
    },
    {
      action: "copy-label",
      label: "Etiketi kopyala",
      hint: "Panoya kopyala",
      disabled: targetId === null,
    },
    {
      action: "focus-selection",
      label: "Seçimi odakla",
      disabled: selectionSize === 0 && targetId === null,
    },
    {
      action: "clear-selection",
      label: "Seçimi temizle",
      hint: "Esc",
      disabled: !hasSelection && targetId === null,
    },
  ];
}

/** Menüde odaklanabilir öğe indeksleri; `aria-disabled` öğeler atlanır. */
export function enabledMenuIndexes(items: readonly ContextMenuItem[]): number[] {
  const indexes: number[] = [];
  items.forEach((item, index) => {
    if (!item.disabled) indexes.push(index);
  });
  return indexes;
}

/**
 * Klavye gezinmesi: ok tuşları etkin öğeler arasında döner. `null` yalnız
 * liste boşsa döner.
 */
export function nextMenuIndex(
  current: number,
  direction: 1 | -1,
  items: readonly ContextMenuItem[]
): number | null {
  const enabled = enabledMenuIndexes(items);
  if (enabled.length === 0) return null;
  const position = enabled.indexOf(current);
  const nextPosition =
    position === -1
      ? direction > 0
        ? 0
        : enabled.length - 1
      : (position + direction + enabled.length) % enabled.length;
  return enabled[nextPosition];
}
