import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-ignore -- Node `--test` ESM için .ts uzantısı zorunlu; apps/web/tsconfig.json allowImportingTsExtensions olmadan reddediyor.
import { escapeXml, sceneToSVG } from "./export.ts";
// @ts-ignore -- Aynı gerekçe.
import { FADED_ALPHA } from "./renderer.ts";
// @ts-ignore -- Aynı gerekçe.
import { createTheme, type GEdge, type GNode } from "./types.ts";

const theme = createTheme();
const viewport = { width: 400, height: 300 };
const camera = { x: 0, y: 0, zoom: 1 };

function node(id: string, label: string, x: number, y: number): GNode {
  return { id, label, kind: "entity", weight: 1, cluster: "k1", x, y, vx: 0, vy: 0 };
}

function edge(id: string, source: string, target: string): GEdge {
  return { id, source, target, type: "related", weight: 2, directed: true };
}

test("export: XML kaçışı tüm özel karakterleri kapsar", () => {
  assert.equal(escapeXml(`<a href="x">Tom & Jerry's</a>`), "&lt;a href=&quot;x&quot;&gt;Tom &amp; Jerry&apos;s&lt;/a&gt;");
});

test("export: SVG sahne, arka plan ve düğümleri içerir", () => {
  const svg = sceneToSVG(
    {
      nodes: [node("a", "Alpha", 0, 0), node("b", "Beta", 100, 0)],
      edges: [edge("ab", "a", "b")],
    },
    camera,
    viewport,
    theme
  );
  assert.ok(svg.startsWith("<svg"));
  assert.ok(svg.includes(`fill="${theme.background}"`));
  assert.ok(svg.includes(`data-node-id="a"`));
  assert.ok(svg.includes(`data-edge-id="ab"`));
  assert.ok(svg.includes("Alpha"));
  assert.ok(svg.includes("<path"));
  assert.ok(svg.endsWith("</svg>"));
});

test("export: etiketler ve kimlikler XML kaçışından geçer", () => {
  const svg = sceneToSVG(
    {
      nodes: [node("a<b>", `<script>alert("x")</script> & 'tırnak'`, 0, 0)],
      edges: [],
    },
    camera,
    viewport,
    theme
  );
  assert.ok(!svg.includes("<script>"));
  assert.ok(svg.includes("&lt;script&gt;"));
  assert.ok(svg.includes("data-node-id=\"a&lt;b&gt;\""));
});

test("export: seçim vurgusu ve soluklaştırma uygulanır", () => {
  const svg = sceneToSVG(
    {
      nodes: [node("a", "A", 0, 0), node("b", "B", 100, 0), node("c", "C", 200, 0)],
      edges: [edge("ab", "a", "b"), edge("bc", "b", "c")],
      selectedId: "a",
    },
    camera,
    viewport,
    theme
  );
  assert.ok(svg.includes(theme.accent));
  assert.ok(svg.includes(`opacity="${theme.dimAlpha}"`));
});

test("export: seçim kutusu ve küme halesi çizilir", () => {
  const svg = sceneToSVG(
    {
      nodes: [node("a", "A", 0, 0), node("b", "B", 40, 0)],
      edges: [],
      selectionRect: { x: 10, y: 10, width: 50, height: 30 },
    },
    camera,
    viewport,
    theme
  );
  assert.ok(svg.includes(`stroke="${theme.selectionBox}"`));
  assert.ok(svg.includes(`fill="${theme.cluster}"`));
  assert.ok(svg.includes("<circle"));
});

test("export: kenar kendi rengini ve yol vurgusunu taşır", () => {
  const colored: GEdge = { ...edge("ab", "a", "b"), color: "#abcdef" };
  const plain = sceneToSVG(
    { nodes: [node("a", "A", 0, 0), node("b", "B", 100, 0)], edges: [colored] },
    camera,
    viewport,
    theme
  );
  assert.ok(plain.includes(`stroke="#abcdef"`));

  const path = sceneToSVG(
    {
      nodes: [node("a", "A", 0, 0), node("b", "B", 100, 0)],
      edges: [edge("ab", "a", "b")],
      pathNodeIds: ["a", "b"],
      pathEdgeIds: ["ab"],
    },
    camera,
    viewport,
    theme
  );
  assert.ok(path.includes(`stroke="${theme.path}"`));
});

test("export: kesikli ve soluk öğeler SVG'ye taşınır", () => {
  const svg = sceneToSVG(
    {
      nodes: [{ ...node("a", "A", 0, 0), dashed: true, faded: true }],
      edges: [{ ...edge("ab", "a", "a"), dashed: true, faded: true }],
    },
    camera,
    viewport,
    theme
  );
  assert.ok(svg.includes('stroke-dasharray="5 4"'));
  assert.ok(svg.includes(`opacity="${FADED_ALPHA.toFixed(2)}"`));
});

test("export: kenar etiketi yalnız yakınlaştırılmış LOD'da çizilir", () => {
  const scene = {
    nodes: [node("a", "A", 0, 0), node("b", "B", 100, 0)],
    edges: [edge("ab", "a", "b")],
  };
  const near = sceneToSVG(scene, { ...camera, zoom: 1 }, viewport, theme);
  assert.ok(!near.includes(">related<"));
  const far = sceneToSVG(scene, { ...camera, zoom: 2 }, viewport, theme, { labels: false });
  assert.ok(far.includes(">related<"));
});
