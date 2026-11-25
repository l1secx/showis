import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-ignore -- Node `--test` ESM için .ts uzantısı zorunlu; apps/web/tsconfig.json allowImportingTsExtensions olmadan reddediyor.
import { collapseGraphData, groupOfProxy, hubIdOfProxy, hubProxyId, planHubCollapse, proxyLabel, resolveHubMember, withoutExpandedHubs } from "./hubs.ts";
// @ts-ignore -- Aynı gerekçe.
import type { GraphData, GraphNodeInput } from "./types.ts";

/** Hub + 6 yaprak + iki uçlu çekirdek kenarı. */
function starGraph(): GraphData {
  const nodes: GraphNodeInput[] = [
    { id: "hub", label: "Hub", kind: "person", weight: 30 },
    { id: "core", label: "Çekirdek", kind: "person", weight: 20 },
    { id: "second", label: "İkinci", kind: "person", weight: 15 },
  ];
  for (let index = 0; index < 6; index += 1) {
    nodes.push({ id: `leaf-${index}`, label: `Yaprak ${index}`, kind: "platform", weight: 4 });
  }
  const edges = [
    { id: "e-core", source: "hub", target: "core", type: "same_person", weight: 2 },
    { id: "e-second", source: "core", target: "second", type: "same_person", weight: 2 },
    ...Array.from({ length: 6 }, (_, index) => ({
      id: `e-leaf-${index}`,
      source: "hub",
      target: `leaf-${index}`,
      type: "has_account",
      weight: 1,
    })),
  ];
  return { nodes, edges };
}

test("hubs: proxy kimliği gidiş-dönüşü", () => {
  assert.equal(hubProxyId("hub"), "hub-plus:hub");
  assert.equal(hubIdOfProxy("hub-plus:hub"), "hub");
  assert.equal(hubIdOfProxy("hub"), null);
  assert.equal(hubIdOfProxy("hub-plus:"), null);
});

test("hubs: yıldız topolojisinde yapraklar tek grupta toplanır", () => {
  const data = starGraph();
  const groups = planHubCollapse(data.nodes, data.edges);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].hubId, "hub");
  assert.equal(groups[0].leafIds.length, 6);
  assert.equal(groups[0].proxyId, "hub-plus:hub");
  assert.equal(proxyLabel(6), "+6");
});

test("hubs: eşik altı hub daraltılmaz", () => {
  const data = starGraph();
  const groups = planHubCollapse(data.nodes, data.edges, { minLeaves: 10 });
  assert.deepEqual(groups, []);
});

test("hubs: çoğu yaprak olmayan düğüm hub sayılmaz", () => {
  const nodes: GraphNodeInput[] = [];
  for (let index = 0; index < 10; index += 1) {
    nodes.push({ id: `n-${index}`, label: `Düğüm ${index}`, kind: "person", weight: 10 });
  }
  // n-0'a 4 yaprak + 5 çekirdek komşu: çekirdekler kendi zincirleriyle
  // derece 2 taşır, yani yaprak değildir. Oran 4/9 < 0.6 → daraltma yok.
  const coreEdges = Array.from({ length: 4 }, (_, index) => ({
    id: `chain-${index}`,
    source: `n-${index + 1}`,
    target: `n-${index + 2}`,
    type: "same_person",
    weight: 1,
  }));
  const edges = [
    ...Array.from({ length: 5 }, (_, index) => ({
      id: `spoke-${index}`,
      source: "n-0",
      target: `n-${index + 1}`,
      type: "same_person",
      weight: 1,
    })),
    ...coreEdges,
    ...Array.from({ length: 4 }, (_, index) => ({
      id: `leaf-${index}`,
      source: "n-0",
      target: `leaf-x${index}`,
      type: "has_account",
      weight: 1,
    })),
  ];
  for (let index = 0; index < 4; index += 1) {
    nodes.push({ id: `leaf-x${index}`, label: `Yaprak ${index}`, kind: "platform", weight: 3 });
  }
  const groups = planHubCollapse(nodes, edges);
  assert.equal(groups.find((group) => group.hubId === "n-0"), undefined);
});

test("hubs: daraltılmış veri yaprakları çıkarır, +N ve hub kenarını ekler", () => {
  const data = starGraph();
  const collapsed = collapseGraphData(data);
  const ids = new Set(collapsed.data.nodes.map((node) => node.id));
  assert.ok(ids.has("hub"));
  assert.ok(ids.has("core"));
  assert.ok(ids.has("hub-plus:hub"));
  assert.ok(!ids.has("leaf-0"));
  assert.equal(collapsed.data.nodes.length, 4);
  const proxyNode = collapsed.data.nodes.find((node) => node.id === "hub-plus:hub");
  assert.equal(proxyNode?.label, "+6");
  assert.equal(proxyNode?.kind, "hub-collapsed");
  const hubEdge = collapsed.data.edges.find((edge) => edge.id === "hub-proxy:hub");
  assert.equal(hubEdge?.source, "hub");
  assert.equal(hubEdge?.target, "hub-plus:hub");
  assert.equal(hubEdge?.dashed, true);
  assert.equal(collapsed.data.edges.length, 3);
  assert.ok(collapsed.groups.has("hub-plus:hub"));
});

test("hubs: açılan hub plan dışında kalır, yapraklar geri döner", () => {
  const data = starGraph();
  const groups = planHubCollapse(data.nodes, data.edges);
  assert.deepEqual(withoutExpandedHubs(groups, new Set(["hub"])), []);
  assert.equal(withoutExpandedHubs(groups, new Set()).length, 1);
  const expanded = collapseGraphData(data, { expandedHubIds: new Set(["hub"]) });
  assert.equal(expanded.groups.size, 0);
  assert.equal(expanded.data.nodes.length, data.nodes.length);
});

test("hubs: grubun yaprağı hub'a çözülür, bilinmeyen kimlik aynen döner", () => {
  const data = starGraph();
  const { groups } = collapseGraphData(data);
  const group = groupOfProxy("hub-plus:hub", groups);
  assert.equal(group?.hubId, "hub");
  assert.equal(groupOfProxy("yok", groups), null);
  assert.equal(resolveHubMember("leaf-3", groups), "hub");
  assert.equal(resolveHubMember("bağımsız", groups), "bağımsız");
});

test("hubs: iki hub'a bağlı düğüm yaprak sayılmaz", () => {
  const nodes: GraphNodeInput[] = [
    { id: "h1", label: "H1", kind: "person", weight: 20 },
    { id: "h2", label: "H2", kind: "person", weight: 19 },
    { id: "bridge", label: "Köprü", kind: "person", weight: 5 },
  ];
  for (let index = 0; index < 4; index += 1) {
    nodes.push({ id: `a-${index}`, label: `A ${index}`, kind: "platform", weight: 2 });
    nodes.push({ id: `b-${index}`, label: `B ${index}`, kind: "platform", weight: 2 });
  }
  const edges = [
    { id: "bridge", source: "h1", target: "h2", type: "same_person", weight: 1 },
    ...Array.from({ length: 4 }, (_, index) => ({
      id: `ea-${index}`,
      source: "h1",
      target: `a-${index}`,
      type: "has_account",
      weight: 1,
    })),
    ...Array.from({ length: 4 }, (_, index) => ({
      id: `eb-${index}`,
      source: "h2",
      target: `b-${index}`,
      type: "has_account",
      weight: 1,
    })),
  ];
  const groups = planHubCollapse(nodes, edges);
  assert.equal(groups.length, 2);
  const h1 = groups.find((group) => group.hubId === "h1");
  assert.deepEqual(h1?.leafIds, ["a-0", "a-1", "a-2", "a-3"]);
  const all = groups.flatMap((group) => group.leafIds);
  assert.equal(new Set(all).size, all.length);
});
