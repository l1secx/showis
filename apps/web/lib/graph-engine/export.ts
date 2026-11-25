/**
 * Dışa aktarım: PNG (canvas.toBlob) ve SVG (aynı sahneden string).
 *
 * SVG üretimi çiziciyle aynı geometri yardımcılarını kullanır; tüm metin ve
 * öznitelikler XML kaçışından geçer.
 */

// @ts-ignore -- Node `--test` ESM için .ts uzantısı zorunlu; apps/web/tsconfig.json allowImportingTsExtensions olmadan reddediyor.
import { worldToScreen } from "./camera.ts";
// @ts-ignore -- Aynı gerekçe.
import { curveOffset, dashPattern, edgeGeometry, edgeLabelVisible, edgeStroke, edgeWidth, FADED_ALPHA, labelVisible, nodeFill, nodeShapeForKind, nodeStroke, rectsOverlap, shapePoints, type RenderScene } from "./renderer.ts";
// @ts-ignore -- Aynı gerekçe.
import type { Camera, GEdge, GNode, Point, Rect, Size, Theme } from "./types.ts";
// @ts-ignore -- Aynı gerekçe.
import { screenNodeRadius } from "./types.ts";

export interface SVGExportOptions {
  /** Ek kenar boşluğu (piksel); varsayılan 0. */
  padding?: number;
  /** Etiketleri dahil et; varsayılan `true`. */
  labels?: boolean;
  /** Etiketler için en düşük yakınlaştırma; varsayılan 0.9. */
  labelMinZoom?: number;
  /** Uzaktan etiketlenen en düşük düğüm ağırlığı; varsayılan 28. */
  labelMinWeight?: number;
  /** Kenar etiketlerini dahil et; varsayılan `true`. */
  edgeLabels?: boolean;
  /** Kenar etiketleri için en düşük yakınlaştırma; varsayılan 1.1. */
  edgeLabelMinZoom?: number;
}

const XML_ESCAPES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&apos;",
};

/** Metni XML güvenli hale getirir. */
export function escapeXml(value: string): string {
  return String(value).replace(/[&<>"']/g, (character) => XML_ESCAPES[character] ?? character);
}

function round(value: number): string {
  if (!Number.isFinite(value)) return "0";
  return value.toFixed(2);
}

/** Sahneyi SVG belgesine çevirir. */
export function sceneToSVG(
  scene: RenderScene,
  camera: Camera,
  viewport: Size,
  theme: Theme,
  options: SVGExportOptions = {}
): string {
  const padding = Math.max(0, options.padding ?? 0);
  const width = Math.max(1, Math.round(viewport.width));
  const height = Math.max(1, Math.round(viewport.height));
  const labels = options.labels ?? true;
  const labelMinZoom = options.labelMinZoom ?? 0.9;
  const labelMinWeight = options.labelMinWeight ?? 28;
  const edgeLabels = (options.edgeLabels ?? true) && edgeLabelVisible(camera.zoom, options.edgeLabelMinZoom ?? 1.1);
  const nodeCount = scene.nodes.length;

  const nodeMap = new Map<string, GNode>();
  const screen = new Map<string, Point>();
  for (const node of scene.nodes) {
    nodeMap.set(node.id, node);
    screen.set(node.id, worldToScreen(camera, viewport, node));
  }

  const selectedId = scene.selectedId ?? null;
  const selectedIds = new Set(scene.selectedIds ?? []);
  if (selectedId) selectedIds.add(selectedId);
  const pathNodes = new Set(scene.pathNodeIds ?? []);
  const pathEdges = new Set(scene.pathEdgeIds ?? []);
  const related = new Set<string>();
  for (const id of selectedIds) {
    related.add(id);
    for (const edge of scene.edges) {
      if (edge.source === id) related.add(edge.target);
      else if (edge.target === id) related.add(edge.source);
    }
  }
  const hasSelection = selectedIds.size > 0;

  const groups = new Map<string, GEdge[]>();
  for (const edge of scene.edges) {
    const key = edge.source < edge.target ? `${edge.source}\u0000${edge.target}` : `${edge.target}\u0000${edge.source}`;
    const group = groups.get(key);
    if (group) group.push(edge);
    else groups.set(key, [edge]);
  }

  const parts: string[] = [];
  parts.push(
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width + padding * 2}" height="${height + padding * 2}" viewBox="${-padding} ${-padding} ${width + padding * 2} ${height + padding * 2}">`
  );
  parts.push(
    `<rect x="${-padding}" y="${-padding}" width="${width + padding * 2}" height="${height + padding * 2}" fill="${escapeXml(theme.background)}"/>`
  );

  // Küme haleleri
  const clusterBounds = new Map<string, { minX: number; minY: number; maxX: number; maxY: number; count: number }>();
  for (const node of scene.nodes) {
    if (!node.cluster) continue;
    const point = screen.get(node.id);
    if (!point) continue;
    const radius = screenNodeRadius(node, camera.zoom, nodeCount);
    const existing = clusterBounds.get(node.cluster);
    if (existing) {
      existing.minX = Math.min(existing.minX, point.x - radius);
      existing.minY = Math.min(existing.minY, point.y - radius);
      existing.maxX = Math.max(existing.maxX, point.x + radius);
      existing.maxY = Math.max(existing.maxY, point.y + radius);
      existing.count += 1;
    } else {
      clusterBounds.set(node.cluster, {
        minX: point.x - radius,
        minY: point.y - radius,
        maxX: point.x + radius,
        maxY: point.y + radius,
        count: 1,
      });
    }
  }
  parts.push(`<g fill="${escapeXml(theme.cluster)}">`);
  for (const bounds of clusterBounds.values()) {
    if (bounds.count < 2) continue;
    const cx = (bounds.minX + bounds.maxX) / 2;
    const cy = (bounds.minY + bounds.maxY) / 2;
    const radius = Math.max(bounds.maxX - bounds.minX, bounds.maxY - bounds.minY) / 2 + 24;
    parts.push(`<circle cx="${round(cx)}" cy="${round(cy)}" r="${round(radius)}"/>`);
  }
  parts.push("</g>");

  // Kenarlar
  const margin = 80;
  const edgeLabelCandidates: { edge: GEdge; point: Point }[] = [];
  const dashAttribute = (dashed?: boolean) => {
    const pattern = dashPattern(dashed);
    return pattern ? ` stroke-dasharray="${pattern.join(" ")}"` : "";
  };
  parts.push('<g fill="none" stroke-linecap="round">');
  for (const group of groups.values()) {
    const groupSize = group.length;
    group.forEach((edge, groupIndex) => {
      const from = screen.get(edge.source);
      const to = screen.get(edge.target);
      if (!from || !to) return;
      if (
        (from.x < -margin && to.x < -margin) ||
        (from.x > width + margin && to.x > width + margin) ||
        (from.y < -margin && to.y < -margin) ||
        (from.y > height + margin && to.y > height + margin)
      ) {
        return;
      }
      if (edge.source === edge.target) {
        const selfNode = nodeMap.get(edge.source);
        const loopRadius = Math.max(6, (selfNode ? screenNodeRadius(selfNode, camera.zoom, nodeCount) : 5) * 0.9);
        const touchesSelf = selectedIds.has(edge.source);
        const selfColor = edgeStroke(theme, edge, {
          selectedId,
          selectedIds,
          path: pathEdges.has(edge.id),
        });
        const selfOpacity = hasSelection && !touchesSelf ? theme.dimAlpha : edge.faded ? FADED_ALPHA : 1;
        parts.push(
          `<circle data-edge-id="${escapeXml(edge.id)}" cx="${round(from.x + loopRadius)}" cy="${round(from.y - loopRadius)}" r="${round(loopRadius)}" stroke="${escapeXml(selfColor)}"${dashAttribute(edge.dashed)} opacity="${round(selfOpacity)}"/>`
        );
        return;
      }
      const sourceNode = nodeMap.get(edge.source);
      const targetNode = nodeMap.get(edge.target);
      const geometry = edgeGeometry(
        from,
        to,
        sourceNode ? screenNodeRadius(sourceNode, camera.zoom, nodeCount) : 5,
        targetNode ? screenNodeRadius(targetNode, camera.zoom, nodeCount) : 5,
        curveOffset(groupIndex, groupSize)
      );
      const touchesSelection = selectedIds.has(edge.source) || selectedIds.has(edge.target);
      const isPath = pathEdges.has(edge.id);
      const dimEdge = hasSelection && !touchesSelection;
      const color = edgeStroke(theme, edge, { selectedId, selectedIds, path: isPath });
      const strokeWidth = edgeWidth(edge, { emphasis: touchesSelection || isPath });
      const opacity = dimEdge ? theme.dimAlpha : edge.faded ? FADED_ALPHA : 1;
      parts.push(
        `<path data-edge-id="${escapeXml(edge.id)}" d="M ${round(geometry.start.x)} ${round(geometry.start.y)} Q ${round(geometry.control.x)} ${round(geometry.control.y)} ${round(geometry.end.x)} ${round(geometry.end.y)}" stroke="${escapeXml(color)}" stroke-width="${round(strokeWidth)}"${dashAttribute(edge.dashed)} opacity="${round(opacity)}"/>`
      );
      if (edge.directed) {
        const angle = geometry.angle;
        const size = Math.max(3.2, 4 + camera.zoom);
        const left = {
          x: geometry.end.x - Math.cos(angle - 0.45) * size,
          y: geometry.end.y - Math.sin(angle - 0.45) * size,
        };
        const right = {
          x: geometry.end.x - Math.cos(angle + 0.45) * size,
          y: geometry.end.y - Math.sin(angle + 0.45) * size,
        };
        parts.push(
          `<path d="M ${round(geometry.end.x)} ${round(geometry.end.y)} L ${round(left.x)} ${round(left.y)} L ${round(right.x)} ${round(right.y)} Z" fill="${escapeXml(color)}" opacity="${round(opacity)}"/>`
        );
      }
      if (edgeLabels && edge.type) {
        edgeLabelCandidates.push({
          edge,
          point: {
            x: (geometry.start.x + 2 * geometry.control.x + geometry.end.x) / 4,
            y: (geometry.start.y + 2 * geometry.control.y + geometry.end.y) / 4,
          },
        });
      }
    });
  }
  parts.push("</g>");

  // Düğümler ve etiketler
  const occupied: Rect[] = [];
  for (const node of scene.nodes) {
    const point = screen.get(node.id);
    if (!point) continue;
    const radius = screenNodeRadius(node, camera.zoom, nodeCount);
    if (point.x + radius < -margin || point.x - radius > width + margin) continue;
    if (point.y + radius < -margin || point.y - radius > height + margin) continue;
    const isSelected = selectedIds.has(node.id);
    const isRelated = !hasSelection || related.has(node.id);
    const opacity = !isRelated ? theme.dimAlpha : node.faded ? FADED_ALPHA : 1;
    const stroke = nodeStroke(theme, node, { selectedId, selectedIds, path: pathNodes.has(node.id) });
    const strokeWidth = isSelected ? 2.2 : pathNodes.has(node.id) ? 2 : 1.1;
    const fill = nodeFill(theme, node, isSelected ? "selected" : pathNodes.has(node.id) ? "path" : "normal");
    const attributes = `data-node-id="${escapeXml(node.id)}" fill="${escapeXml(fill)}" stroke="${escapeXml(stroke)}" stroke-width="${strokeWidth}"${dashAttribute(node.dashed)} opacity="${round(opacity)}"`;
    const shape = nodeShapeForKind(node.kind);
    const points = shapePoints(shape, point.x, point.y, radius);
    if (!points) {
      parts.push(`<circle cx="${round(point.x)}" cy="${round(point.y)}" r="${round(radius)}" ${attributes}/>`);
    } else {
      const path = points.map((p, index) => `${index === 0 ? "M" : "L"} ${round(p.x)} ${round(p.y)}`).join(" ") + " Z";
      parts.push(`<path d="${path}" ${attributes}/>`);
    }
    const important = isSelected || (hasSelection && related.has(node.id));
    const showLabel =
      labels &&
      node.label &&
      labelVisible(camera.zoom, labelMinZoom, {
        weight: node.weight,
        minWeight: labelMinWeight,
        important,
      });
    if (showLabel) {
      const textColor = isSelected ? theme.accent : isRelated ? theme.label : theme.labelMuted;
      const labelX = point.x + radius + 6;
      const labelWidth = node.label.length * 6.4;
      occupied.push({ x: labelX, y: point.y - 8, width: labelWidth, height: 16 });
      parts.push(
        `<text x="${round(labelX)}" y="${round(point.y)}" fill="${escapeXml(textColor)}" font-family="${escapeXml(theme.fontFamily)}" font-size="${isSelected ? 13 : 12}" dominant-baseline="middle" opacity="${round(isRelated ? 1 : theme.dimAlpha)}">${escapeXml(node.label)}</text>`
      );
    }
  }

  // Kenar etiketleri: yakınlaştırılmış LOD, çakışma kırpmalı.
  for (const candidate of edgeLabelCandidates) {
    const textWidth = candidate.edge.type.length * 5.9;
    const rect: Rect = {
      x: candidate.point.x - textWidth / 2 - 3,
      y: candidate.point.y - 7,
      width: textWidth + 6,
      height: 14,
    };
    if (rect.x + rect.width < 0 || rect.x > width) continue;
    if (rect.y + rect.height < 0 || rect.y > height) continue;
    if (occupied.some((existing) => rectsOverlap(rect, existing))) continue;
    occupied.push(rect);
    const touchesSelection =
      selectedIds.has(candidate.edge.source) || selectedIds.has(candidate.edge.target);
    const textOpacity = hasSelection && !touchesSelection ? theme.dimAlpha : 1;
    parts.push(
      `<text x="${round(rect.x + 3)}" y="${round(candidate.point.y)}" fill="${escapeXml(theme.labelMuted)}" font-family="${escapeXml(theme.fontFamily)}" font-size="11" dominant-baseline="middle" opacity="${round(textOpacity)}">${escapeXml(candidate.edge.type)}</text>`
    );
  }

  // Seçim kutusu
  if (scene.selectionRect) {
    const rect = scene.selectionRect;
    parts.push(
      `<rect x="${round(rect.x)}" y="${round(rect.y)}" width="${round(rect.width)}" height="${round(rect.height)}" fill="none" stroke="${escapeXml(theme.selectionBox)}" stroke-width="1" stroke-dasharray="5 4"/>`
    );
  }

  parts.push("</svg>");
  return parts.join("\n");
}

/** Canvas'ı PNG blobuna çevirir. */
export function canvasToPNG(
  canvas: HTMLCanvasElement,
  type = "image/png",
  quality?: number
): Promise<Blob> {
  return new Promise((resolve, reject) => {
    if (typeof canvas.toBlob !== "function") {
      reject(new Error("Bu ortamda canvas.toBlob desteklenmiyor."));
      return;
    }
    canvas.toBlob(
      (blob) => {
        if (blob) resolve(blob);
        else reject(new Error("PNG dışa aktarımı başarısız oldu."));
      },
      type,
      quality
    );
  });
}
