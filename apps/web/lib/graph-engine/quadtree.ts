/**
 * Barnes-Hut kuvvet ağacı (saf TypeScript, DOM bağımsız).
 *
 * `computeForces` her gövde için itme kuvvetini O(n log n) hesaplar. `theta`
 * büyüdükçe yaklaşıklık artar, maliyet düşer. Kendi gövdesi hesaba katılmaz.
 */

export interface Body {
  x: number;
  y: number;
  mass: number;
}

export interface Force {
  fx: number;
  fy: number;
}

export interface QuadBounds {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

const MAX_DEPTH = 24;
const EPSILON = 1e-9;

class QuadNode {
  readonly x0: number;
  readonly y0: number;
  readonly x1: number;
  readonly y1: number;
  readonly depth: number;
  mass = 0;
  comX = 0;
  comY = 0;
  bodies: number[] | null = [];
  children: QuadNode[] | null = null;

  constructor(x0: number, y0: number, x1: number, y1: number, depth: number) {
    this.x0 = x0;
    this.y0 = y0;
    this.x1 = x1;
    this.y1 = y1;
    this.depth = depth;
  }
}

export class BarnesHutTree {
  private readonly leafCapacity: number;
  private root: QuadNode | null = null;
  private bodyCount = 0;
  private cellCount = 0;

  constructor(leafCapacity = 1) {
    this.leafCapacity = Math.max(1, Math.floor(leafCapacity));
  }

  /** Ağaçtaki gövde sayısı. */
  get size(): number {
    return this.bodyCount;
  }

  /** Ağaç hücre sayısı (testte üst sınır doğrulaması için). */
  get cells(): number {
    return this.cellCount;
  }

  get bounds(): QuadBounds | null {
    if (!this.root) return null;
    return {
      minX: this.root.x0,
      minY: this.root.y0,
      maxX: this.root.x1,
      maxY: this.root.y1,
    };
  }

  build(bodies: readonly Body[]): void {
    this.bodyCount = bodies.length;
    this.cellCount = 0;
    if (bodies.length === 0) {
      this.root = null;
      return;
    }

    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    for (const body of bodies) {
      if (body.x < minX) minX = body.x;
      if (body.y < minY) minY = body.y;
      if (body.x > maxX) maxX = body.x;
      if (body.y > maxY) maxY = body.y;
    }
    if (!Number.isFinite(minX) || !Number.isFinite(minY)) {
      this.root = null;
      this.bodyCount = 0;
      return;
    }

    // Kare hücre: yarıçap hesabı iki eksende aynı olsun.
    const span = Math.max(maxX - minX, maxY - minY, EPSILON);
    const pad = span * 0.01 + EPSILON;
    const root = new QuadNode(minX - pad, minY - pad, minX - pad + span + pad * 2, minY - pad + span + pad * 2, 0);
    this.root = root;
    this.cellCount = 1;

    for (let index = 0; index < bodies.length; index += 1) {
      this.insert(root, index, bodies);
    }
    this.accumulate(root, bodies);
  }

  /**
   * `bodyIndex` gövdesine etkiyen toplam kuvvet. Pozitif `strength` iter,
   * negatif `strength` çeker.
   */
  addForce(
    bodyIndex: number,
    bodies: readonly Body[],
    theta: number,
    strength: number,
    softening: number
  ): Force {
    const root = this.root;
    if (!root || root.mass === 0) return { fx: 0, fy: 0 };
    const body = bodies[bodyIndex];
    const px = body.x;
    const py = body.y;
    const soft = Math.max(0, softening);
    const thetaSq = Math.max(0, theta) * Math.max(0, theta);
    let fx = 0;
    let fy = 0;

    const stack: QuadNode[] = [root];
    while (stack.length > 0) {
      const node = stack.pop() as QuadNode;
      if (node.mass === 0) continue;
      const dx = node.comX - px;
      const dy = node.comY - py;
      const distSq = dx * dx + dy * dy;
      const denom = distSq + soft;
      if (denom <= 0) continue;
      const dist = Math.sqrt(distSq);
      const openness = distSq > EPSILON ? ((node.x1 - node.x0) * (node.x1 - node.x0)) / denom : Infinity;
      const isLeaf = node.children === null;

      if (!isLeaf && openness >= thetaSq) {
        for (const child of node.children as QuadNode[]) {
          if (child.mass !== 0) stack.push(child);
        }
        continue;
      }

      if (isLeaf && node.bodies && node.bodies.length > 0) {
        for (const index of node.bodies) {
          if (index === bodyIndex) continue;
          const other = bodies[index];
          const odx = other.x - px;
          const ody = other.y - py;
          const odistSq = odx * odx + ody * ody;
          const odistance = Math.sqrt(odistSq);
          const odenom = odistSq + soft;
          if (odenom <= 0 || odistance < EPSILON) continue;
          const factor = (strength * other.mass) / odenom / odistance;
          fx += odx * factor;
          fy += ody * factor;
        }
        continue;
      }

      if (distSq <= EPSILON) continue;
      // Yaprak dalı ile aynı kuvvet yasası: |F| = strength * mass / (mesafe² + softening).
      const factor = (strength * node.mass) / denom / dist;
      fx += dx * factor;
      fy += dy * factor;
    }

    return { fx, fy };
  }

  private insert(node: QuadNode, index: number, bodies: readonly Body[]): void {
    const body = bodies[index];

    if (node.children) {
      this.insert(this.childFor(node, body.x, body.y), index, bodies);
      return;
    }

    const list = node.bodies as number[];
    list.push(index);
    if (
      list.length <= this.leafCapacity ||
      node.depth >= MAX_DEPTH ||
      node.x1 - node.x0 <= EPSILON
    ) {
      return;
    }

    node.children = this.subdivide(node);
    node.bodies = null;
    for (const existing of list) {
      this.insert(this.childFor(node, bodies[existing].x, bodies[existing].y), existing, bodies);
    }
  }

  private childFor(node: QuadNode, x: number, y: number): QuadNode {
    const children = node.children as QuadNode[];
    const midX = (node.x0 + node.x1) / 2;
    const midY = (node.y0 + node.y1) / 2;
    return x < midX ? (y < midY ? children[0] : children[2]) : y < midY ? children[1] : children[3];
  }

  private subdivide(node: QuadNode): QuadNode[] {
    const midX = (node.x0 + node.x1) / 2;
    const midY = (node.y0 + node.y1) / 2;
    const depth = node.depth + 1;
    const children = [
      new QuadNode(node.x0, node.y0, midX, midY, depth),
      new QuadNode(midX, node.y0, node.x1, midY, depth),
      new QuadNode(node.x0, midY, midX, node.y1, depth),
      new QuadNode(midX, midY, node.x1, node.y1, depth),
    ];
    this.cellCount += children.length;
    return children;
  }

  private accumulate(node: QuadNode, bodies: readonly Body[]): void {
    if (node.children) {
      let mass = 0;
      let comX = 0;
      let comY = 0;
      for (const child of node.children) {
        this.accumulate(child, bodies);
        if (child.mass === 0) continue;
        mass += child.mass;
        comX += child.comX * child.mass;
        comY += child.comY * child.mass;
      }
      node.mass = mass;
      node.comX = mass > 0 ? comX / mass : 0;
      node.comY = mass > 0 ? comY / mass : 0;
      return;
    }

    const list = node.bodies as number[] | null;
    if (!list || list.length === 0) {
      node.mass = 0;
      node.comX = 0;
      node.comY = 0;
      return;
    }
    let mass = 0;
    let comX = 0;
    let comY = 0;
    for (const index of list) {
      const body = bodies[index];
      mass += body.mass;
      comX += body.x * body.mass;
      comY += body.y * body.mass;
    }
    node.mass = mass;
    node.comX = mass > 0 ? comX / mass : 0;
    node.comY = mass > 0 ? comY / mass : 0;
  }
}

/** Ağacı kurup her gövde için kuvveti döndürür. */
export function computeForces(
  bodies: readonly Body[],
  options: { theta?: number; strength?: number; softening?: number } = {}
): Force[] {
  const tree = new BarnesHutTree();
  tree.build(bodies);
  const theta = options.theta ?? 0.9;
  const strength = options.strength ?? 1;
  const softening = options.softening ?? 1;
  const out: Force[] = new Array(bodies.length);
  for (let index = 0; index < bodies.length; index += 1) {
    out[index] = tree.addForce(index, bodies, theta, strength, softening);
  }
  return out;
}

/** Test ve karşılaştırma için doğrudan (O(n²)) kuvvet hesabı. */
export function bruteForce(
  bodies: readonly Body[],
  options: { strength?: number; softening?: number } = {}
): Force[] {
  const strength = options.strength ?? 1;
  const softening = options.softening ?? 1;
  return bodies.map((body, index) => {
    let fx = 0;
    let fy = 0;
    for (let otherIndex = 0; otherIndex < bodies.length; otherIndex += 1) {
      if (otherIndex === index) continue;
      const other = bodies[otherIndex];
      const dx = other.x - body.x;
      const dy = other.y - body.y;
      const distSq = dx * dx + dy * dy;
      const distance = Math.sqrt(distSq);
      const denom = distSq + softening;
      if (denom <= 0 || distance < 1e-9) continue;
      const factor = (strength * other.mass) / denom / distance;
      fx += dx * factor;
      fy += dy * factor;
    }
    return { fx, fy };
  });
}
