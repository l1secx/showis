/**
 * Hub daraltma: çok sayıda yaprak komşusu olan düğümün yaprakları tek bir
 * "+N" düğümünde toplanır. Saf fonksiyonlar; DOM veya canvas gerektirmez.
 *
 * Sözleşme:
 * - Yaprak: derecesi 1 olan düğümdür (yalnız hub'a bağlı).
 * - Hub: yaprak komşu sayısı `minLeaves` eşiğine ulaşan düğüm.
 * - Her yaprak en fazla bir hub'a atanır; iki hub'a bağlı düğüm yaprak değildir.
 * - "+N" düğümü `hub-plus:<hubId>` kimliğini taşır ve motor tıklamayı
 *   `GraphCanvas` üzerinden sayfaya bildirir; sayfa hub'ı açar.
 */

// @ts-ignore -- Node `--test` ESM için .ts uzantısı zorunlu; apps/web/tsconfig.json allowImportingTsExtensions olmadan reddediyor.
import type { GraphData, GraphEdgeInput, GraphNodeInput } from "./types.ts";

export const HUB_PROXY_PREFIX = "hub-plus:";

export interface HubCollapseOptions {
  /** Daraltma için gereken en az yaprak komşu sayısı; varsayılan 4. */
  minLeaves?: number;
  /**
   * Hub derecesinin en az bu oranı yaprak olmalı; varsayılan 0.6.
   * Yıldız topolojisini hedefler, çekirdek ağ düğümlerini değil.
   */
  minLeafRatio?: number;
}

export interface HubGroup {
  /** Hub düğüm kimliği. */
  hubId: string;
  /** Toplanan yaprak kimlikleri. */
  leafIds: string[];
  /** "+N" düğümünün kimliği; deterministiktir. */
  proxyId: string;
}

type HubNodeInput = Pick<GraphNodeInput, "id" | "label" | "weight"> &
  Partial<Pick<GraphNodeInput, "kind" | "cluster" | "meta" | "x" | "y" | "fixed">>;

interface Candidate {
  hubId: string;
  leafIds: string[];
  weight: number;
}

function degreeMap(
  edges: readonly Pick<GraphEdgeInput, "source" | "target">[]
): Map<string, number> {
  const degree = new Map<string, number>();
  for (const edge of edges) {
    degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1);
    if (edge.target !== edge.source) {
      degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1);
    }
  }
  return degree;
}

/** "+N" düğüm kimliği. */
export function hubProxyId(hubId: string): string {
  return `${HUB_PROXY_PREFIX}${hubId}`;
}

/** "+N" düğümünden hub kimliğini çözer; proxy değilse `null`. */
export function hubIdOfProxy(id: string): string | null {
  if (!id.startsWith(HUB_PROXY_PREFIX)) return null;
  const hubId = id.slice(HUB_PROXY_PREFIX.length);
  return hubId.length > 0 ? hubId : null;
}

/**
 * Daraltma adaylarını hesaplar. Aday listesi deterministiktir (hub ağırlığı,
 * sonra kimlik). Aynı yaprak iki hub'a da yapraksa hiçbirine atanmaz.
 */
export function planHubCollapse(
  nodes: readonly HubNodeInput[],
  edges: readonly Pick<GraphEdgeInput, "source" | "target">[],
  options: HubCollapseOptions = {}
): HubGroup[] {
  const minLeaves = Math.max(2, Math.floor(options.minLeaves ?? 4));
  const minLeafRatio = Math.min(1, Math.max(0, options.minLeafRatio ?? 0.6));
  const degree = degreeMap(edges);
  const byId = new Map<string, HubNodeInput>();
  for (const node of nodes) byId.set(node.id, node);

  const leavesByHub = new Map<string, string[]>();
  for (const node of nodes) {
    if ((degree.get(node.id) ?? 0) !== 1) continue;
    let neighbor: string | null = null;
    for (const edge of edges) {
      if (edge.source === node.id) {
        neighbor = edge.target;
        break;
      }
      if (edge.target === node.id) {
        neighbor = edge.source;
        break;
      }
    }
    if (!neighbor || neighbor === node.id) continue;
    if ((degree.get(neighbor) ?? 0) <= 1) continue;
    const list = leavesByHub.get(neighbor);
    if (list) list.push(node.id);
    else leavesByHub.set(neighbor, [node.id]);
  }

  const candidates: Candidate[] = [];
  for (const [hubId, leafIds] of leavesByHub) {
    if (leafIds.length < minLeaves) continue;
    const hub = byId.get(hubId);
    if (!hub) continue;
    if (leafIds.length < (degree.get(hubId) ?? 0) * minLeafRatio) continue;
    const sortedLeaves = leafIds.slice().sort((a, b) => {
      const nodeA = byId.get(a);
      const nodeB = byId.get(b);
      return (
        (nodeB?.weight ?? 0) - (nodeA?.weight ?? 0) ||
        (nodeA?.label ?? a).localeCompare(nodeB?.label ?? b, "tr") ||
        a.localeCompare(b)
      );
    });
    candidates.push({ hubId, leafIds: sortedLeaves, weight: hub.weight });
  }

  candidates.sort((a, b) => b.weight - a.weight || a.hubId.localeCompare(b.hubId));

  const claimed = new Set<string>();
  const groups: HubGroup[] = [];
  for (const candidate of candidates) {
    const available = candidate.leafIds.filter((id) => !claimed.has(id));
    if (available.length < minLeaves) continue;
    for (const id of available) claimed.add(id);
    groups.push({
      hubId: candidate.hubId,
      leafIds: available,
      proxyId: hubProxyId(candidate.hubId),
    });
  }
  return groups;
}

/** Hub'ı açık tutan kimlikleri plan dışında bırakır; açılan yapraklar geri gelir. */
export function withoutExpandedHubs(
  groups: readonly HubGroup[],
  expandedHubIds: ReadonlySet<string>
): HubGroup[] {
  if (expandedHubIds.size === 0) return groups.slice();
  return groups.filter((group) => !expandedHubIds.has(group.hubId));
}

/** "+N" etiketi. */
export function proxyLabel(count: number): string {
  const safe = Number.isFinite(count) ? Math.max(0, Math.floor(count)) : 0;
  return `+${safe}`;
}

export interface CollapsedGraph {
  data: GraphData;
  groups: Map<string, HubGroup>;
}

/**
 * Daraltılmış grafik verisini üretir. Yaprak düğümler ve onların kenarları
 * çıkar; hub ile "+N" arasında tek, kesikli bir kenar eklenir.
 */
export function collapseGraphData(
  data: GraphData,
  options: HubCollapseOptions & { expandedHubIds?: ReadonlySet<string> } = {}
): CollapsedGraph {
  const planned = planHubCollapse(data.nodes, data.edges, options);
  const groupsList = withoutExpandedHubs(planned, options.expandedHubIds ?? new Set());
  if (groupsList.length === 0) {
    return { data, groups: new Map() };
  }

  const hidden = new Set<string>();
  const groups = new Map<string, HubGroup>();
  const proxyByLeaf = new Map<string, string>();
  for (const group of groupsList) {
    groups.set(group.proxyId, group);
    for (const id of group.leafIds) {
      hidden.add(id);
      proxyByLeaf.set(id, group.proxyId);
    }
  }

  const nextNodes: GraphNodeInput[] = data.nodes.filter((node) => !hidden.has(node.id));
  const byId = new Map<string, GraphNodeInput>();
  for (const node of data.nodes) byId.set(node.id, node);

  for (const group of groupsList) {
    const hub = byId.get(group.hubId);
    if (!hub) continue;
    const leafCount = group.leafIds.length;
    const x = Number.isFinite(hub.x) ? (hub.x as number) : undefined;
    const y = Number.isFinite(hub.y) ? (hub.y as number) : undefined;
    nextNodes.push({
      id: group.proxyId,
      label: proxyLabel(leafCount),
      kind: "hub-collapsed",
      // "+N" çipi hub'ın yanında durur; motor yerleşimi gerisini yapar.
      weight: 2 + Math.sqrt(leafCount) * 2,
      cluster: hub.cluster ?? null,
      fixed: true,
      x: x === undefined ? undefined : x + 34,
      y: y === undefined ? undefined : y - 26,
      meta: { hubId: group.hubId, leafCount },
    });
  }

  const nextEdges: GraphEdgeInput[] = [];
  const groupByProxy = new Map<string, HubGroup>();
  for (const group of groupsList) groupByProxy.set(group.proxyId, group);
  // Aynı proxy çiftine düşen kenarlar tekilleşir; ilk kenar temsilcidir.
  const seenProxyPairs = new Set<string>();
  for (const edge of data.edges) {
    const sourceProxy = proxyByLeaf.get(edge.source);
    const targetProxy = proxyByLeaf.get(edge.target);
    if (sourceProxy && targetProxy) continue;
    if (sourceProxy) {
      const group = groupByProxy.get(sourceProxy);
      // Yaprak-hub kenarı, tek "+N" kenarıyla temsil edilir.
      if (group && edge.target === group.hubId) continue;
      const key = `${sourceProxy}\u0000${edge.target}`;
      if (seenProxyPairs.has(key)) continue;
      seenProxyPairs.add(key);
      nextEdges.push({
        ...edge,
        id: `${edge.id}:collapsed`,
        source: sourceProxy,
        target: edge.target,
        dashed: true,
        weight: Math.max(1, edge.weight),
      });
      continue;
    }
    if (targetProxy) {
      const group = groupByProxy.get(targetProxy);
      if (group && edge.source === group.hubId) continue;
      const key = `${edge.source}\u0000${targetProxy}`;
      if (seenProxyPairs.has(key)) continue;
      seenProxyPairs.add(key);
      nextEdges.push({
        ...edge,
        id: `${edge.id}:collapsed`,
        source: edge.source,
        target: targetProxy,
        dashed: true,
        weight: Math.max(1, edge.weight),
      });
      continue;
    }
    nextEdges.push(edge);
  }

  for (const group of groupsList) {
    nextEdges.push({
      id: `hub-proxy:${group.hubId}`,
      source: group.hubId,
      target: group.proxyId,
      type: "collapsed",
      weight: 1,
      dashed: true,
    });
  }

  return { data: { nodes: nextNodes, edges: nextEdges }, groups };
}

/** Proxy kimliğini içeren grubu döndürür; yoksa `null`. */
export function groupOfProxy(
  proxyId: string,
  groups: ReadonlyMap<string, HubGroup>
): HubGroup | null {
  return groups.get(proxyId) ?? null;
}

/**
 * Yaprak kimliğini hub'a çözer; seçim veya yol uçları için kullanılır.
 * Bilinmeyen kimlik aynen döner.
 */
export function resolveHubMember(
  id: string,
  groups: ReadonlyMap<string, HubGroup>
): string {
  for (const group of groups.values()) {
    if (group.leafIds.includes(id)) return group.hubId;
  }
  return id;
}
