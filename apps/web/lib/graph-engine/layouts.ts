/**
 * Statik yerleşimler: hepsi `Map<düğümId, hedef konum>` üretir. Motor bu
 * hedeflere animasyonlu geçiş yapar. DOM bağımlılığı yoktur.
 */

// @ts-ignore -- Node `--test` ESM için .ts uzantısı zorunlu; apps/web/tsconfig.json allowImportingTsExtensions olmadan reddediyor.
import type { GEdge, GNode, GraphEdgeInput, GraphNodeInput, Point } from "./types.ts";

export type LayoutName = "radial" | "hierarchical" | "circle" | "grid";
export type LayoutTargets = Map<string, Point>;

export interface LayoutOptions {
  /** Radial yerleşimin merkez düğümü. */
  center?: string | null;
  /** Halka/katman arası mesafe. */
  spacing?: number;
  /** Düğümlerin yerleşeceği alanın başlangıcı. */
  origin?: Point;
}

type AnyNode = Pick<GNode, "id"> | GraphNodeInput;
type AnyEdge = Pick<GEdge, "source" | "target"> | GraphEdgeInput;

const DEFAULT_SPACING = 140;

function nodeIds(nodes: readonly AnyNode[]): string[] {
  return nodes.map((node) => node.id);
}

function adjacency(nodes: readonly AnyNode[], edges: readonly AnyEdge[]): Map<string, string[]> {
  const map = new Map<string, string[]>();
  for (const id of nodeIds(nodes)) map.set(id, []);
  for (const edge of edges) {
    const source = map.get(edge.source);
    const target = map.get(edge.target);
    if (!source || !target) continue;
    source.push(edge.target);
    target.push(edge.source);
  }
  return map;
}

function sortNeighbors(map: Map<string, string[]>): void {
  for (const list of map.values()) list.sort();
}

function degrees(map: Map<string, string[]>): Map<string, number> {
  const result = new Map<string, number>();
  for (const [id, list] of map) result.set(id, list.length);
  return result;
}

/** BFS seviyeleri; bağlantısız düğümler haritada yoktur. */
function bfsLevels(start: string, map: Map<string, string[]>): Map<string, number> {
  const levels = new Map<string, number>();
  levels.set(start, 0);
  const queue: string[] = [start];
  for (let head = 0; head < queue.length; head += 1) {
    const id = queue[head];
    const level = levels.get(id) ?? 0;
    for (const neighbor of map.get(id) ?? []) {
      if (levels.has(neighbor)) continue;
      levels.set(neighbor, level + 1);
      queue.push(neighbor);
    }
  }
  return levels;
}

function pickCenter(nodes: readonly AnyNode[], map: Map<string, string[]>, preferred?: string | null): string {
  if (preferred) {
    const match = nodes.find((node) => node.id === preferred);
    if (match) return match.id;
  }
  let best = nodes[0]?.id ?? "";
  let bestDegree = -1;
  const degreeMap = degrees(map);
  for (const node of nodes) {
    const degree = degreeMap.get(node.id) ?? 0;
    if (degree > bestDegree) {
      best = node.id;
      bestDegree = degree;
    }
  }
  return best;
}

/**
 * Seçili merkezden BFS halkaları. Aynı seviyedeki düğümler eşit açı aralığıyla
 * yerleşir; bağlantısız düğümler en dış halkaya dağıtılır.
 */
export function radialLayout(
  nodes: readonly AnyNode[],
  edges: readonly AnyEdge[],
  options: LayoutOptions = {}
): LayoutTargets {
  const targets: LayoutTargets = new Map();
  if (nodes.length === 0) return targets;
  const ids = nodeIds(nodes);
  const map = adjacency(nodes, edges);
  sortNeighbors(map);
  const center = pickCenter(nodes, map, options.center);
  const spacing = options.spacing ?? DEFAULT_SPACING;
  const origin = options.origin ?? { x: 0, y: 0 };
  const levels = bfsLevels(center, map);

  let maxLevel = 0;
  for (const level of levels.values()) if (level > maxLevel) maxLevel = level;
  const orphanLevel = maxLevel + 1;

  const rings = new Map<number, string[]>();
  for (const id of ids) {
    let level = levels.get(id);
    if (level === undefined) level = orphanLevel;
    const ring = rings.get(level);
    if (ring) ring.push(id);
    else rings.set(level, [id]);
  }

  for (const [level, ring] of rings) {
    if (level === 0) {
      const id = ring[0];
      targets.set(id, { x: origin.x, y: origin.y });
      continue;
    }
    const radius = level * spacing;
    const step = (Math.PI * 2) / Math.max(ring.length, 1);
    const phase = -Math.PI / 2;
    ring.forEach((id, index) => {
      const angle = phase + index * step;
      targets.set(id, {
        x: origin.x + Math.cos(angle) * radius,
        y: origin.y + Math.sin(angle) * radius,
      });
    });
  }
  return targets;
}

/** Katmanlı yerleşim: derinlik y, sıra x. */
export function hierarchicalLayout(
  nodes: readonly AnyNode[],
  edges: readonly AnyEdge[],
  options: LayoutOptions = {}
): LayoutTargets {
  const targets: LayoutTargets = new Map();
  if (nodes.length === 0) return targets;
  const spacing = options.spacing ?? DEFAULT_SPACING;
  const origin = options.origin ?? { x: 0, y: 0 };
  const map = adjacency(nodes, edges);
  sortNeighbors(map);

  const depths = new Map<string, number>();
  const order = nodeIds(nodes);
  const visited = new Set<string>();
  const roots: string[] = [];

  for (const id of order) {
    // Kendisine yönelen kenar yoksa kök kabul et.
    const incoming = edges.some((edge) => edge.target === id && map.has(edge.source));
    if (!incoming) roots.push(id);
  }
  const starts = roots.length > 0 ? roots : order.slice(0, 1);

  for (const start of starts) {
    if (visited.has(start)) continue;
    const queue: string[] = [start];
    depths.set(start, depths.get(start) ?? 0);
    visited.add(start);
    for (let head = 0; head < queue.length; head += 1) {
      const id = queue[head];
      const depth = depths.get(id) ?? 0;
      for (const neighbor of map.get(id) ?? []) {
        if (visited.has(neighbor)) continue;
        visited.add(neighbor);
        depths.set(neighbor, depth + 1);
        queue.push(neighbor);
      }
    }
  }
  for (const id of order) if (!depths.has(id)) depths.set(id, 0);

  const layers = new Map<number, string[]>();
  for (const id of order) {
    const depth = depths.get(id) ?? 0;
    const layer = layers.get(depth);
    if (layer) layer.push(id);
    else layers.set(depth, [id]);
  }

  for (const [depth, layer] of layers) {
    const y = origin.y + depth * spacing;
    const width = (layer.length - 1) * spacing;
    layer.forEach((id, index) => {
      targets.set(id, { x: origin.x + index * spacing - width / 2, y });
    });
  }
  return targets;
}

/** Düğümleri eşit açı aralığıyla tek halkaya dizer. */
export function circleLayout(
  nodes: readonly AnyNode[],
  _edges: readonly AnyEdge[] = [],
  options: LayoutOptions = {}
): LayoutTargets {
  const targets: LayoutTargets = new Map();
  const count = nodes.length;
  if (count === 0) return targets;
  const spacing = options.spacing ?? DEFAULT_SPACING;
  const origin = options.origin ?? { x: 0, y: 0 };
  const radius = Math.max(spacing, (count * spacing) / (Math.PI * 2));
  const step = (Math.PI * 2) / count;
  nodes.forEach((node, index) => {
    const angle = -Math.PI / 2 + index * step;
    targets.set(node.id, {
      x: origin.x + Math.cos(angle) * radius,
      y: origin.y + Math.sin(angle) * radius,
    });
  });
  return targets;
}

/** Kareye yakın ızgara. */
export function gridLayout(
  nodes: readonly AnyNode[],
  _edges: readonly AnyEdge[] = [],
  options: LayoutOptions = {}
): LayoutTargets {
  const targets: LayoutTargets = new Map();
  const count = nodes.length;
  if (count === 0) return targets;
  const spacing = options.spacing ?? DEFAULT_SPACING;
  const origin = options.origin ?? { x: 0, y: 0 };
  const columns = Math.max(1, Math.ceil(Math.sqrt(count)));
  const rows = Math.ceil(count / columns);
  const offsetX = ((columns - 1) * spacing) / 2;
  const offsetY = ((rows - 1) * spacing) / 2;
  nodes.forEach((node, index) => {
    const column = index % columns;
    const row = Math.floor(index / columns);
    targets.set(node.id, {
      x: origin.x + column * spacing - offsetX,
      y: origin.y + row * spacing - offsetY,
    });
  });
  return targets;
}

export const LAYOUTS: Record<
  LayoutName,
  (nodes: readonly AnyNode[], edges: readonly AnyEdge[], options?: LayoutOptions) => LayoutTargets
> = {
  radial: radialLayout,
  hierarchical: hierarchicalLayout,
  circle: circleLayout,
  grid: gridLayout,
};

export function computeLayout(
  name: LayoutName,
  nodes: readonly AnyNode[],
  edges: readonly AnyEdge[],
  options: LayoutOptions = {}
): LayoutTargets {
  return LAYOUTS[name](nodes, edges, options);
}

export function isLayoutName(value: string): value is LayoutName {
  return value === "radial" || value === "hierarchical" || value === "circle" || value === "grid";
}
