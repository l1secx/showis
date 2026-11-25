/**
 * Hit-test için ızgara (grid) uzamsal indeksi.
 *
 * Düğümler yarıçaplarıyla, kenarlar segment sınır kutusuyla hücrelere yazılır.
 * Saf TypeScript; çizim veya DOM bilgisi yoktur.
 */

// @ts-ignore -- Node `--test` ESM için .ts uzantısı zorunlu; apps/web/tsconfig.json allowImportingTsExtensions olmadan reddediyor.
import type { BBox, GEdge, GNode, Rect } from "./types.ts";
// @ts-ignore -- Aynı gerekçe.
import { nodeRadius } from "./types.ts";

const DEFAULT_CELL_SIZE = 64;

function cellKey(cx: number, cy: number): string {
  return `${cx}:${cy}`;
}

function cellCoord(value: number, cellSize: number): number {
  return Math.floor(value / cellSize);
}

export interface NodeHit {
  node: GNode;
  distance: number;
}

export interface EdgeHit {
  edge: GEdge;
  distance: number;
}

export class SpatialIndex {
  readonly cellSize: number;
  private readonly nodeCells = new Map<string, GNode[]>();
  private readonly edgeCells = new Map<string, GEdge[]>();
  private nodes: readonly GNode[] = [];
  private edges: readonly GEdge[] = [];
  private nodeById = new Map<string, GNode>();

  constructor(cellSize = DEFAULT_CELL_SIZE) {
    this.cellSize = Math.max(8, cellSize);
  }

  get size(): number {
    return this.nodes.length;
  }

  /** Düğümleri (ve isteğe bağlı kenarları) yeniden indeksler. */
  rebuild(nodes: readonly GNode[], edges: readonly GEdge[] = []): void {
    this.nodes = nodes;
    this.edges = edges;
    this.nodeCells.clear();
    this.edgeCells.clear();
    this.nodeById.clear();

    for (const node of nodes) {
      if (!Number.isFinite(node.x) || !Number.isFinite(node.y)) continue;
      this.nodeById.set(node.id, node);
      const radius = nodeRadius(node);
      const minX = cellCoord(node.x - radius, this.cellSize);
      const maxX = cellCoord(node.x + radius, this.cellSize);
      const minY = cellCoord(node.y - radius, this.cellSize);
      const maxY = cellCoord(node.y + radius, this.cellSize);
      for (let cx = minX; cx <= maxX; cx += 1) {
        for (let cy = minY; cy <= maxY; cy += 1) {
          const key = cellKey(cx, cy);
          const bucket = this.nodeCells.get(key);
          if (bucket) bucket.push(node);
          else this.nodeCells.set(key, [node]);
        }
      }
    }

    for (const edge of edges) {
      const source = this.nodeById.get(edge.source);
      const target = this.nodeById.get(edge.target);
      if (!source || !target) continue;
      const minX = cellCoord(Math.min(source.x, target.x) - 1, this.cellSize);
      const maxX = cellCoord(Math.max(source.x, target.x) + 1, this.cellSize);
      const minY = cellCoord(Math.min(source.y, target.y) - 1, this.cellSize);
      const maxY = cellCoord(Math.max(source.y, target.y) + 1, this.cellSize);
      const cellCount = (maxX - minX + 1) * (maxY - minY + 1);
      // Aşırı uzun kenar tüm ızgarayı doldurmasın: bbox taraması ile sınırla.
      if (cellCount > 4096) {
        for (let cx = minX; cx <= maxX; cx += 1) {
          const key = cellKey(cx, minY);
          const bucket = this.edgeCells.get(key);
          if (bucket) bucket.push(edge);
          else this.edgeCells.set(key, [edge]);
        }
        continue;
      }
      for (let cx = minX; cx <= maxX; cx += 1) {
        for (let cy = minY; cy <= maxY; cy += 1) {
          const key = cellKey(cx, cy);
          const bucket = this.edgeCells.get(key);
          if (bucket) bucket.push(edge);
          else this.edgeCells.set(key, [edge]);
        }
      }
    }
  }

  /** Verilen daire içindeki düğümler (yarıçap dahil). */
  queryCircle(x: number, y: number, radius: number): GNode[] {
    const found: GNode[] = [];
    const seen = new Set<string>();
    const minX = cellCoord(x - radius, this.cellSize);
    const maxX = cellCoord(x + radius, this.cellSize);
    const minY = cellCoord(y - radius, this.cellSize);
    const maxY = cellCoord(y + radius, this.cellSize);
    for (let cx = minX; cx <= maxX; cx += 1) {
      for (let cy = minY; cy <= maxY; cy += 1) {
        const bucket = this.nodeCells.get(cellKey(cx, cy));
        if (!bucket) continue;
        for (const node of bucket) {
          if (seen.has(node.id)) continue;
          seen.add(node.id);
          const distance = Math.hypot(node.x - x, node.y - y);
          if (distance <= radius + nodeRadius(node)) found.push(node);
        }
      }
    }
    return found;
  }

  nodesInRect(rect: Rect): GNode[] {
    const found: GNode[] = [];
    const seen = new Set<string>();
    const minX = cellCoord(rect.x, this.cellSize);
    const maxX = cellCoord(rect.x + rect.width, this.cellSize);
    const minY = cellCoord(rect.y, this.cellSize);
    const maxY = cellCoord(rect.y + rect.height, this.cellSize);
    for (let cx = minX; cx <= maxX; cx += 1) {
      for (let cy = minY; cy <= maxY; cy += 1) {
        const bucket = this.nodeCells.get(cellKey(cx, cy));
        if (!bucket) continue;
        for (const node of bucket) {
          if (seen.has(node.id)) continue;
          seen.add(node.id);
          if (
            node.x >= rect.x &&
            node.x <= rect.x + rect.width &&
            node.y >= rect.y &&
            node.y <= rect.y + rect.height
          ) {
            found.push(node);
          }
        }
      }
    }
    return found;
  }

  /** En yakın düğüm; `pad` toleransı yarıçapa eklenir. */
  hitNode(x: number, y: number, pad = 0): GNode | null {
    const hits = this.nodeHits(x, y, pad);
    return hits.length > 0 ? hits[0].node : null;
  }

  nodeHits(x: number, y: number, pad = 0): NodeHit[] {
    const hits: NodeHit[] = [];
    for (const node of this.queryCircle(x, y, pad)) {
      const radius = nodeRadius(node);
      const distance = Math.hypot(node.x - x, node.y - y);
      if (distance <= radius + pad) hits.push({ node, distance });
    }
    hits.sort((a, b) => a.distance - b.distance);
    return hits;
  }

  /** En yakın kenar; `tolerance` dünya birimidir. */
  hitEdge(x: number, y: number, tolerance = 6): GEdge | null {
    const hit = this.edgeHit(x, y, tolerance);
    return hit ? hit.edge : null;
  }

  edgeHit(x: number, y: number, tolerance = 6): EdgeHit | null {
    let best: EdgeHit | null = null;
    const seen = new Set<string>();
    const minX = cellCoord(x - tolerance, this.cellSize);
    const maxX = cellCoord(x + tolerance, this.cellSize);
    const minY = cellCoord(y - tolerance, this.cellSize);
    const maxY = cellCoord(y + tolerance, this.cellSize);
    for (let cx = minX; cx <= maxX; cx += 1) {
      for (let cy = minY; cy <= maxY; cy += 1) {
        const bucket = this.edgeCells.get(cellKey(cx, cy));
        if (!bucket) continue;
        for (const edge of bucket) {
          if (seen.has(edge.id)) continue;
          seen.add(edge.id);
          const source = this.nodeById.get(edge.source);
          const target = this.nodeById.get(edge.target);
          if (!source || !target) continue;
          const distance = pointToSegmentDistance(x, y, source.x, source.y, target.x, target.y);
          if (distance > tolerance) continue;
          if (!best || distance < best.distance) best = { edge, distance };
        }
      }
    }
    return best;
  }

  get edgesIndexed(): readonly GEdge[] {
    return this.edges;
  }
}

/** Noktadan doğru parçasına en kısa mesafe. */
export function pointToSegmentDistance(
  px: number,
  py: number,
  ax: number,
  ay: number,
  bx: number,
  by: number
): number {
  const dx = bx - ax;
  const dy = by - ay;
  const lengthSq = dx * dx + dy * dy;
  if (lengthSq <= 1e-12) return Math.hypot(px - ax, py - ay);
  let t = ((px - ax) * dx + (py - ay) * dy) / lengthSq;
  t = t < 0 ? 0 : t > 1 ? 1 : t;
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

/** Düğüm listesinden sınır kutusu (spatial yardımcı). */
export function bboxOfIndexedNodes(nodes: readonly GNode[]): BBox | null {
  if (nodes.length === 0) return null;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const node of nodes) {
    const radius = nodeRadius(node);
    minX = Math.min(minX, node.x - radius);
    minY = Math.min(minY, node.y - radius);
    maxX = Math.max(maxX, node.x + radius);
    maxY = Math.max(maxY, node.y + radius);
  }
  if (!Number.isFinite(minX)) return null;
  return { minX, minY, maxX, maxY };
}
