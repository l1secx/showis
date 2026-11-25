/**
 * Kuvvet tabanlı yerleşim simülasyonu (DOM bağımsız, deterministik).
 *
 * Kuvvetler: Barnes-Hut many-body itme, yay (link), merkezleme, çarpışma,
 * küme çekimi. Aynı girdi + aynı `seed` her zaman aynı yerleşimi üretir.
 */

// @ts-ignore -- Node `--test` ESM için .ts uzantısı zorunlu; apps/web/tsconfig.json allowImportingTsExtensions olmadan reddediyor.
import { BarnesHutTree, type Body } from "./quadtree.ts";
// @ts-ignore -- Aynı gerekçe.
import { SpatialIndex } from "./spatial-index.ts";
// @ts-ignore -- Aynı gerekçe.
import type { GEdge, GNode, Point } from "./types.ts";
// @ts-ignore -- Aynı gerekçe.
import { clamp, nodeRadius } from "./types.ts";

export interface SimulationOptions {
  /** Deterministik başlangıç konumları için tohum. */
  seed?: number;
  /** İtme kuvveti büyüklüğü. */
  chargeStrength?: number;
  /** Barnes-Hut açıklık eşiği; küçük değer daha doğru, daha yavaş. */
  theta?: number;
  softening?: number;
  linkDistance?: number;
  linkStrength?: number;
  centerStrength?: number;
  collisionStrength?: number;
  clusterStrength?: number;
  /** Küme çapa noktalarının yerleşeceği halka yarıçapı. */
  clusterRadius?: number;
  velocityDecay?: number;
  alphaDecay?: number;
  alphaMin?: number;
  maxVelocity?: number;
}

interface ResolvedOptions {
  seed: number;
  chargeStrength: number;
  theta: number;
  softening: number;
  linkDistance: number;
  linkStrength: number;
  centerStrength: number;
  collisionStrength: number;
  clusterStrength: number;
  clusterRadius: number;
  velocityDecay: number;
  alphaDecay: number;
  alphaMin: number;
  maxVelocity: number;
}

const DEFAULTS: ResolvedOptions = {
  seed: 1,
  chargeStrength: 1200,
  theta: 0.9,
  softening: 60,
  linkDistance: 90,
  linkStrength: 0.08,
  centerStrength: 0.0025,
  collisionStrength: 0.6,
  clusterStrength: 0.03,
  clusterRadius: 240,
  velocityDecay: 0.38,
  alphaDecay: 0.0228,
  alphaMin: 0.001,
  maxVelocity: 28,
};

export const MAX_FORCE = 8;

function resolve(options: SimulationOptions): ResolvedOptions {
  return {
    seed: options.seed ?? DEFAULTS.seed,
    chargeStrength: options.chargeStrength ?? DEFAULTS.chargeStrength,
    theta: options.theta ?? DEFAULTS.theta,
    softening: options.softening ?? DEFAULTS.softening,
    linkDistance: options.linkDistance ?? DEFAULTS.linkDistance,
    linkStrength: options.linkStrength ?? DEFAULTS.linkStrength,
    centerStrength: options.centerStrength ?? DEFAULTS.centerStrength,
    collisionStrength: options.collisionStrength ?? DEFAULTS.collisionStrength,
    clusterStrength: options.clusterStrength ?? DEFAULTS.clusterStrength,
    clusterRadius: options.clusterRadius ?? DEFAULTS.clusterRadius,
    velocityDecay: options.velocityDecay ?? DEFAULTS.velocityDecay,
    alphaDecay: options.alphaDecay ?? DEFAULTS.alphaDecay,
    alphaMin: options.alphaMin ?? DEFAULTS.alphaMin,
    maxVelocity: options.maxVelocity ?? DEFAULTS.maxVelocity,
  };
}

/** Tohumlu, hızlı ve tekrarlanabilir RNG (mulberry32). */
export function mulberry32(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * Konumu olmayan düğümlere tohumlu, altın açı spirali ile başlangıç konumu
 * verir. Verilen konumlar korunur.
 */
export function seedPositions(nodes: readonly GNode[], seed = 1, radius = 0): void {
  const count = nodes.length;
  if (count === 0) return;
  const random = mulberry32(seed);
  const spread = radius > 0 ? radius : Math.max(80, 16 * Math.sqrt(count));
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));
  for (let index = 0; index < count; index += 1) {
    const node = nodes[index];
    if (Number.isFinite(node.x) && Number.isFinite(node.y)) continue;
    const r = spread * Math.sqrt((index + 0.5) / count);
    const angle = index * goldenAngle;
    node.x = Math.cos(angle) * r + (random() - 0.5) * 0.5;
    node.y = Math.sin(angle) * r + (random() - 0.5) * 0.5;
    node.vx = 0;
    node.vy = 0;
  }
}

/** Küme kimliklerinden deterministik çapa noktaları üretir. */
export function clusterAnchors(nodes: readonly GNode[], radius: number): Map<string, Point> {
  const anchors = new Map<string, Point>();
  const ids = Array.from(new Set(nodes.map((node) => node.cluster).filter((id): id is string => Boolean(id))));
  ids.sort();
  if (ids.length === 0) return anchors;
  const step = (Math.PI * 2) / ids.length;
  ids.forEach((id, index) => {
    const angle = -Math.PI / 2 + index * step;
    anchors.set(id, { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius });
  });
  return anchors;
}

export class Simulation {
  readonly nodes: GNode[];
  readonly edges: GEdge[];
  readonly options: ResolvedOptions;
  alpha = 1;

  private readonly indexById = new Map<string, number>();
  private readonly bodies: Body[];
  private readonly tree = new BarnesHutTree();
  private readonly fx: Float64Array;
  private readonly fy: Float64Array;
  private readonly collisionIndex = new SpatialIndex(48);
  private readonly anchors: Map<string, Point>;
  private maxRadius = 8;
  private tickCount = 0;
  private lastMaxSpeed = 0;

  constructor(nodes: GNode[], edges: GEdge[], options: SimulationOptions = {}) {
    this.nodes = nodes;
    this.edges = edges;
    this.options = resolve(options);
    seedPositions(nodes, this.options.seed);

    this.fx = new Float64Array(nodes.length);
    this.fy = new Float64Array(nodes.length);
    this.bodies = nodes.map((node) => ({ x: node.x, y: node.y, mass: 1 }));

    nodes.forEach((node, index) => {
      this.indexById.set(node.id, index);
      const radius = nodeRadius(node);
      if (radius > this.maxRadius) this.maxRadius = radius;
      if (!Number.isFinite(node.x)) node.x = 0;
      if (!Number.isFinite(node.y)) node.y = 0;
      if (!Number.isFinite(node.vx)) node.vx = 0;
      if (!Number.isFinite(node.vy)) node.vy = 0;
    });

    this.anchors = clusterAnchors(nodes, this.options.clusterRadius);
  }

  get iterations(): number {
    return this.tickCount;
  }

  /** Son adımdaki en büyük hız büyüklüğü (yakınsama ölçütü). */
  get maxSpeed(): number {
    return this.lastMaxSpeed;
  }

  /** `count` adım ilerletir. */
  tick(count = 1): void {
    const steps = Math.max(0, Math.floor(count));
    for (let step = 0; step < steps; step += 1) {
      if (this.alpha <= 0) break;
      this.step();
    }
  }

  isSettled(): boolean {
    if (this.nodes.length === 0) return true;
    return this.alpha <= this.options.alphaMin || (this.alpha < 0.04 && this.lastMaxSpeed < 0.05);
  }

  reheat(alpha = 1): void {
    this.alpha = clamp(alpha, 0, 1);
  }

  /** Simülasyonu durdurur (statik yerleşimden sonra). */
  freeze(): void {
    this.alpha = 0;
    this.lastMaxSpeed = 0;
  }

  /** Dışarıdan (yerleşim geçişi) konum yazıldıktan sonra hızları sıfırlar. */
  resetVelocities(): void {
    for (const node of this.nodes) {
      node.vx = 0;
      node.vy = 0;
    }
  }

  private step(): void {
    const nodes = this.nodes;
    const count = nodes.length;
    if (count === 0) {
      this.alpha = 0;
      return;
    }
    const { fx, fy, bodies, tree, indexById } = this;
    const alpha = this.alpha;
    const options = this.options;

    fx.fill(0);
    fy.fill(0);

    for (let index = 0; index < count; index += 1) {
      const node = nodes[index];
      bodies[index].x = node.x;
      bodies[index].y = node.y;
    }
    tree.build(bodies);

    // Many-body (Barnes-Hut)
    const charge = options.chargeStrength * alpha;
    for (let index = 0; index < count; index += 1) {
      const force = tree.addForce(index, bodies, options.theta, charge, options.softening);
      fx[index] += force.fx;
      fy[index] += force.fy;
    }

    // Yay kuvveti
    const linkStrength = options.linkStrength * alpha;
    for (const edge of this.edges) {
      const sourceIndex = indexById.get(edge.source);
      const targetIndex = indexById.get(edge.target);
      if (sourceIndex === undefined || targetIndex === undefined) continue;
      const source = nodes[sourceIndex];
      const target = nodes[targetIndex];
      let dx = target.x - source.x;
      let dy = target.y - source.y;
      let distance = Math.hypot(dx, dy);
      if (distance < 1e-6) {
        dx = 1e-6;
        dy = 0;
        distance = 1e-6;
      }
      const delta = ((distance - options.linkDistance) / distance) * linkStrength;
      const forceX = clamp(dx * delta, -MAX_FORCE, MAX_FORCE);
      const forceY = clamp(dy * delta, -MAX_FORCE, MAX_FORCE);
      fx[sourceIndex] += forceX;
      fy[sourceIndex] += forceY;
      fx[targetIndex] -= forceX;
      fy[targetIndex] -= forceY;
    }

    // Küme çekimi
    if (this.anchors.size > 0 && options.clusterStrength > 0) {
      const pull = options.clusterStrength * alpha;
      for (let index = 0; index < count; index += 1) {
        const cluster = nodes[index].cluster;
        if (!cluster) continue;
        const anchor = this.anchors.get(cluster);
        if (!anchor) continue;
        fx[index] += (anchor.x - nodes[index].x) * pull;
        fy[index] += (anchor.y - nodes[index].y) * pull;
      }
    }

    // Merkezleme
    const center = options.centerStrength * alpha;
    if (center > 0) {
      for (let index = 0; index < count; index += 1) {
        fx[index] -= nodes[index].x * center;
        fy[index] -= nodes[index].y * center;
      }
    }

    // Çarpışma (ızgara indeksi)
    const collisionStrength = options.collisionStrength * alpha;
    if (collisionStrength > 0) {
      this.applyCollisions(collisionStrength);
    }

    // Entegrasyon
    const damping = 1 - options.velocityDecay;
    const maxVelocity = options.maxVelocity;
    let maxSpeed = 0;
    for (let index = 0; index < count; index += 1) {
      const node = nodes[index];
      if (node.fixed) {
        node.vx = 0;
        node.vy = 0;
        continue;
      }
      let vx = (node.vx + fx[index]) * damping;
      let vy = (node.vy + fy[index]) * damping;
      if (vx > maxVelocity) vx = maxVelocity;
      else if (vx < -maxVelocity) vx = -maxVelocity;
      if (vy > maxVelocity) vy = maxVelocity;
      else if (vy < -maxVelocity) vy = -maxVelocity;
      node.vx = vx;
      node.vy = vy;
      node.x += vx;
      node.y += vy;
      const speed = Math.abs(vx) + Math.abs(vy);
      if (speed > maxSpeed) maxSpeed = speed;
    }
    this.lastMaxSpeed = maxSpeed;
    this.tickCount += 1;

    this.alpha += -this.alpha * options.alphaDecay;
    if (this.alpha < 1e-4) this.alpha = 0;
  }

  private applyCollisions(strength: number): void {
    const { nodes, collisionIndex, indexById } = this;
    collisionIndex.rebuild(nodes);
    const reach = this.maxRadius * 2 + 4;
    for (let index = 0; index < nodes.length; index += 1) {
      const node = nodes[index];
      const candidates = collisionIndex.queryCircle(node.x, node.y, reach);
      for (const other of candidates) {
        const otherIndex = indexById.get(other.id);
        if (otherIndex === undefined || otherIndex <= index) continue;
        let dx = other.x - node.x;
        let dy = other.y - node.y;
        let distance = Math.hypot(dx, dy);
        if (distance < 1e-6) {
          // Aynı noktada: belirleyici yön.
          dx = 1e-3;
          dy = 0;
          distance = 1e-3;
        }
        const minimum = nodeRadius(node) + nodeRadius(other) + 4;
        const overlap = minimum - distance;
        if (overlap <= 0) continue;
        const push = Math.min(overlap * 0.5 * strength, 4);
        const nx = (dx / distance) * push;
        const ny = (dy / distance) * push;
        if (!node.fixed) {
          node.x -= nx;
          node.y -= ny;
          node.vx -= nx * 0.5;
          node.vy -= ny * 0.5;
        }
        if (!other.fixed) {
          other.x += nx;
          other.y += ny;
          other.vx += nx * 0.5;
          other.vy += ny * 0.5;
        }
      }
    }
  }
}
