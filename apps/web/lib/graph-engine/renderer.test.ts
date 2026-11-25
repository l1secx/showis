import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-ignore -- Node `--test` ESM için .ts uzantısı zorunlu; apps/web/tsconfig.json allowImportingTsExtensions olmadan reddediyor.
import { curveOffset, colorWithAlpha, dashPattern, edgeGeometry, edgeLabelVisible, edgeStroke, edgeWidth, FADED_ALPHA, labelVisible, nodeFill, nodeShapeForKind, nodeStroke, rectsOverlap, shapePoints } from "./renderer.ts";
// @ts-ignore -- Aynı gerekçe.
import { createTheme, MIN_SCREEN_NODE_RADIUS, nodeDensityScale, nodeKindColor, screenNodeRadius } from "./types.ts";

const theme = createTheme();

test("renderer: kind -> şekil eşlemesi", () => {
  assert.equal(nodeShapeForKind("platform"), "square");
  assert.equal(nodeShapeForKind("finding"), "diamond");
  assert.equal(nodeShapeForKind("evidence"), "hexagon");
  assert.equal(nodeShapeForKind("entity"), "circle");
  assert.equal(nodeShapeForKind("bilinmeyen"), "circle");
});

test("renderer: daire nokta listesi döndürmez, çokgenler köşe döndürür", () => {
  assert.equal(shapePoints("circle", 0, 0, 10), null);
  assert.equal(shapePoints("square", 0, 0, 10)?.length, 4);
  assert.equal(shapePoints("diamond", 0, 0, 10)?.length, 4);
  const hexagon = shapePoints("hexagon", 0, 0, 10);
  assert.equal(hexagon?.length, 6);
  for (const point of hexagon ?? []) {
    assert.ok(Math.abs(Math.hypot(point.x, point.y) - 10) < 1e-9);
  }
});

test("renderer: çoklu kenar kaydırması simetrik", () => {
  assert.equal(curveOffset(0, 1), 0);
  assert.equal(curveOffset(0, 3) + curveOffset(2, 3), 0);
  assert.equal(curveOffset(1, 3), 0);
  assert.ok(curveOffset(2, 3) > 0);
});

test("renderer: kenar geometrisi düğüm yarıçapı kadar kısaltır", () => {
  const geometry = edgeGeometry({ x: 0, y: 0 }, { x: 100, y: 0 }, 10, 20, 0);
  assert.ok(Math.abs(geometry.start.x - 10) < 1e-9);
  assert.ok(Math.abs(geometry.end.x - 80) < 1e-9);
  assert.ok(Math.abs(geometry.control.x - 50) < 1e-9);
  assert.ok(Math.abs(geometry.control.y) < 1e-9);
  assert.ok(Math.abs(geometry.angle) < 1e-9);
});

test("renderer: kavis kaydırması kenarı dikey yönde büker", () => {
  const geometry = edgeGeometry({ x: 0, y: 0 }, { x: 100, y: 0 }, 0, 0, 20);
  // Ekran uzayında +y aşağı: pozitif kaydırma kenarı aşağı büker.
  assert.ok(Math.abs(geometry.control.y - 20) < 1e-9);
});

test("renderer: kenar rengi seçim > yol > hover > soluk > kendi rengi", () => {
  const edge = { source: "a", target: "b", color: "#123456" };
  assert.equal(edgeStroke(theme, edge), "#123456");
  assert.equal(edgeStroke(theme, edge, { selectedId: "a" }), theme.accent);
  assert.equal(edgeStroke(theme, edge, { selectedId: "c" }), theme.edgeMuted);
  assert.equal(edgeStroke(theme, edge, { path: true }), theme.path);
  assert.equal(edgeStroke(theme, edge, { hovered: true }), theme.accent);
  assert.equal(edgeStroke(theme, { ...edge, color: undefined }), theme.edge);
});

test("renderer: düğüm konturu seçim/hover > yol > düğüm rengi", () => {
  const node = { id: "a", color: "#abcdef" };
  assert.equal(nodeStroke(theme, node), "#abcdef");
  assert.equal(nodeStroke(theme, node, { selectedId: "a" }), theme.accent);
  assert.equal(nodeStroke(theme, node, { hovered: true }), theme.accent);
  assert.equal(nodeStroke(theme, node, { path: true }), theme.path);
  assert.equal(nodeStroke(theme, { id: "b" }), theme.nodeStroke);
});

test("renderer: kenar kalınlığı vurgu ve yoğunlukla ölçeklenir", () => {
  const base = edgeWidth({ weight: 2 });
  assert.ok(base > 0.35);
  assert.ok(edgeWidth({ weight: 2 }, { emphasis: true }) > base);
  assert.ok(edgeWidth({ weight: 2 }, { dense: true }) < base);
  assert.equal(edgeWidth({ weight: -100 }), 0.55);
});

test("renderer: tedarik zinciri türleri renk ve şekle eşlenir", () => {
  const kinds = ["company", "supplier", "product", "person", "location", "domain", "other"];
  const colors = new Set(kinds.map((kind) => nodeKindColor(kind)));
  assert.equal(colors.size, kinds.length);
  for (const kind of kinds) {
    assert.match(nodeKindColor(kind), /^#[0-9a-f]{6}$/i, kind);
  }
  assert.equal(nodeKindColor("bilinmeyen"), nodeKindColor("other"));
  assert.equal(nodeShapeForKind("company"), "hexagon");
  assert.equal(nodeShapeForKind("supplier"), "square");
  assert.equal(nodeShapeForKind("product"), "diamond");
  assert.equal(nodeShapeForKind("person"), "circle");
  assert.equal(nodeShapeForKind("location"), "triangle");
  assert.equal(nodeShapeForKind("domain"), "square");
  assert.equal(nodeShapeForKind("other"), "circle");
  assert.equal(nodeShapeForKind("Şirket"), "circle");
});

test("renderer: üçgen şekli üç köşe döndürür", () => {
  const triangle = shapePoints("triangle", 0, 0, 10);
  assert.equal(triangle?.length, 3);
  assert.ok(Math.abs((triangle?.[0].y ?? 0) + 10) < 1e-9);
});

test("renderer: kesikli çizgi ve soluk opaklık yardımcıları", () => {
  assert.equal(dashPattern(false), null);
  assert.deepEqual(dashPattern(true), [5, 4]);
  assert.ok(FADED_ALPHA > 0 && FADED_ALPHA < 1);
});

test("renderer: kenar etiketi LOD eşiği ve çakışma kırpması", () => {
  assert.equal(edgeLabelVisible(0.9, 1.1), false);
  assert.equal(edgeLabelVisible(1.1, 1.1), true);
  assert.equal(edgeLabelVisible(Number.NaN, 1.1), false);
  const base = { x: 0, y: 0, width: 10, height: 10 };
  assert.ok(rectsOverlap(base, { x: 5, y: 5, width: 10, height: 10 }));
  assert.ok(!rectsOverlap(base, { x: 10, y: 0, width: 10, height: 10 }));
  assert.ok(!rectsOverlap(base, { x: 0, y: 10, width: 10, height: 10 }));
});

test("renderer: düğüm yarıçapı zoom ve düğüm sayısıyla ölçeklenir", () => {
  const node = { weight: 20 };
  const near = screenNodeRadius(node, 1.6, 135);
  const far = screenNodeRadius(node, 0.25, 135);
  assert.ok(near > far, "yakın zoom daha büyük yarıçap");
  assert.ok(far >= MIN_SCREEN_NODE_RADIUS);
  const at135 = screenNodeRadius({ weight: 44 }, 0.5, 135);
  assert.ok(at135 >= 5 && at135 <= 8, `135 düğümde ~5-7px, gelen ${at135}`);
  const crowded = screenNodeRadius(node, 1, 900) < screenNodeRadius(node, 1, 20);
  assert.ok(crowded, "kalabalık grafikte baz yarıçap küçülür");
  assert.equal(screenNodeRadius({ weight: 1 }, 1e-9, 5000), MIN_SCREEN_NODE_RADIUS);
  assert.equal(nodeDensityScale(10), 1);
  assert.ok(nodeDensityScale(135) < 1);
});

test("renderer: etiket LOD ağırlık, seçim ve komşuya göre açılır", () => {
  assert.equal(labelVisible(0.4, 0.9, { weight: 4 }), false);
  assert.equal(labelVisible(0.5, 0.9, { weight: 40, minWeight: 28 }), true);
  assert.equal(labelVisible(0.9, 0.9, { weight: 4 }), true);
  assert.equal(labelVisible(0.1, 0.9, { weight: 1, important: true }), true);
  assert.equal(labelVisible(Number.NaN, 0.9, { important: true }), false);
});

test("renderer: düğüm dolgusu tür renginin düşük opaklıkta tonudur", () => {
  assert.equal(colorWithAlpha("#abcdef", 0.2), "rgba(171,205,239,0.200)");
  assert.equal(colorWithAlpha("#abc", 0.5), "rgba(170,187,204,0.500)");
  assert.equal(colorWithAlpha("rgba(10, 20, 30, 0.9)", 0.4), "rgba(10,20,30,0.400)");
  assert.equal(colorWithAlpha("bozuk", 0.4), "bozuk");
  const node = { weight: 20, color: "#60a5fa" };
  const normal = nodeFill(theme, node);
  const selected = nodeFill(theme, node, "selected");
  assert.match(normal, /^rgba\(96,165,250,0\.\d+\)$/);
  assert.notEqual(normal, selected);
  assert.notEqual(normal, theme.nodeFill);
});
