/**
 * Olay normalizasyonu testleri (`node --test lib/timeline/*.test.ts`).
 */

import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-ignore -- Node `--test` ESM için .ts uzantısı zorunlu; apps/web/tsconfig.json allowImportingTsExtensions olmadan reddediyor.
import { auditTrack, evidenceTrack, filterEvents, groupTracks, normalizeEvents, parseTime, sourceLabel } from "./model.ts";

test("parseTime: ISO ve tarih dizesi epoch ms'e döner", () => {
  assert.equal(parseTime("2024-05-01"), Date.UTC(2024, 4, 1));
  assert.equal(parseTime("2024-05-01T10:30:00Z"), Date.UTC(2024, 4, 1, 10, 30));
  assert.equal(parseTime(null), null);
  assert.equal(parseTime(""), null);
  assert.equal(parseTime("geçersiz"), null);
});

test("auditTrack: aksiyon önekine göre şerit seçer", () => {
  assert.equal(auditTrack("scan.start").track, "system:scan");
  assert.equal(auditTrack("scan.completed").track, "system:scan");
  assert.equal(auditTrack("connector.task").track, "system:scan");
  assert.equal(auditTrack("research.session.create").track, "system:research");
  assert.equal(auditTrack("pool.item.update").track, "system:pool");
  assert.equal(auditTrack("reasoning.correlate").track, "system:reasoning");
  assert.equal(auditTrack("report.create").track, "system:report");
  assert.equal(auditTrack("case.create").track, "system:case");
  assert.equal(auditTrack("bilinmeyen.aksiyon").track, "system:audit");
});

test("evidenceTrack: kaynak etiketini Türkçe üretir, bilinmeyeni gizlemez", () => {
  assert.equal(evidenceTrack("research").track, "evidence:research");
  assert.equal(evidenceTrack("research").label, "Araştırma kanıtı");
  assert.equal(evidenceTrack(null).track, "evidence:other");
  assert.match(evidenceTrack("yeni-kaynak").label, /yeni-kaynak/);
});

test("normalizeEvents: dört kaynağı tek listede zamana göre birleştirir", () => {
  const events = normalizeEvents(
    {
      audit: [
        {
          id: "a1",
          action: "scan.start",
          actor: "operator",
          target: "Scan: 9",
          created_at: "2024-05-01T09:00:00Z",
        },
      ],
      findings: [
        {
          id: "f1",
          platform: "github",
          label: "kaneru",
          status: "confirmed",
          score: 0.82,
          url: "https://github.com/kaneru",
          verified_at: "2024-05-03T12:00:00Z",
        },
      ],
      evidence: [
        {
          id: "e1",
          url: "https://ornek.test/a",
          title: "Örnek sayfa",
          source: "research",
          kind: "page",
          http_status: 200,
          fetched_at: "2024-05-02T08:00:00Z",
        },
      ],
      records: [
        {
          item_id: "r1",
          date: "2024-04-01",
          label: "Hesap açıldı",
          place: "İstanbul",
        },
      ],
    },
    { actionLabel: (action) => (action === "scan.start" ? "Tarama başlatıldı" : action) }
  );

  assert.deepEqual(
    events.map((event) => event.id),
    ["record:r1", "audit:a1", "evidence:e1", "finding:f1"]
  );
  assert.equal(events[1].title, "Tarama başlatıldı");
  assert.equal(events[0].track, "record:event");
  assert.equal(events[3].track, "platform:github");
  assert.equal(events[3].detail, "Bulgu doğrulandı · skor 0.82");
  assert.equal(events[2].detail, "page · HTTP 200");
});

test("normalizeEvents: geçersiz tarih düşer, yinelenen kimlik tek kalır", () => {
  const events = normalizeEvents({
    audit: [
      { id: "dup", action: "case.create", created_at: "2024-05-01T00:00:00Z" },
      { id: "dup", action: "case.delete", created_at: "2024-05-02T00:00:00Z" },
      { id: "bad", action: "case.create", created_at: "tarih değil" },
      { id: "empty", action: "case.create", created_at: null },
    ],
  });
  assert.equal(events.length, 1);
  assert.equal(events[0].title, "case.create");
});

test("normalizeEvents: boş girdi boş liste", () => {
  assert.deepEqual(normalizeEvents({}), []);
  assert.deepEqual(normalizeEvents({ audit: [], findings: [], evidence: [], records: [] }), []);
});

test("groupTracks: sayı azalan, eşitlikte Türkçe ada göre sıralar", () => {
  const events = normalizeEvents({
    audit: [
      { id: "1", action: "scan.start", created_at: "2024-05-01T00:00:00Z" },
      { id: "2", action: "scan.completed", created_at: "2024-05-01T01:00:00Z" },
      { id: "3", action: "pool.item.create", created_at: "2024-05-01T02:00:00Z" },
    ],
  });
  const tracks = groupTracks(events);
  assert.deepEqual(
    tracks.map((track) => [track.track, track.count]),
    [
      ["system:scan", 2],
      ["system:pool", 1],
    ]
  );
});

test("filterEvents: gizlenen şeridin olaylarını çıkarır", () => {
  const events = normalizeEvents({
    audit: [
      { id: "1", action: "scan.start", created_at: "2024-05-01T00:00:00Z" },
      { id: "2", action: "pool.item.create", created_at: "2024-05-01T01:00:00Z" },
    ],
  });
  const visible = filterEvents(events, new Set(["system:pool"]));
  assert.deepEqual(
    visible.map((event) => event.id),
    ["audit:1"]
  );
  assert.equal(filterEvents(events, new Set()).length, 2);
});

test("sourceLabel: bilinmeyen kaynak ham adıyla döner", () => {
  assert.equal(sourceLabel("audit"), "Denetim");
  assert.equal(sourceLabel("custom"), "custom");
});
