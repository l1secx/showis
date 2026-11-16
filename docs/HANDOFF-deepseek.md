# Handoff — Showis geliştirme notları

Bu not, aynı depoda çalışan asistana devir için yazıldı.
Amaç: **isim + soyisim (+ okul) verildiğinde anlamlı sonuç üretmek**, sonuçları
Bulgular sekmesine akıtmak, kimlik grafiğini kurmak, tarama süresini önceden
tahmin etmek ve raporu tek sayfada karar verilebilir hale getirmek.

> **Son güncelleme:** 2026-11-17 00:5x (gece kampanyası sonu, T-101 → T-324).
> Önceki sürümdeki "açık işler" listesi büyük ölçüde kapandı; güncel liste aşağıda.

## Bu gece ne değişti (özet)

| Alan | Dosya / konu | Değişiklik |
| --- | --- | --- |
| Tarama hızı | `services/scanner.py`, `showis_engine/{http,ratelimit}.py` | Paralel yürütme (`SHOWIS_SCAN_CONCURRENCY=4`), host limiter, contextvar istek sayacı, HTTP katmanında fail-closed bütçe. **O2: 636 sn → 337 sn (-47 %)**, 140 görev |
| İptal | `services/scanner.py`, `api/v1/scans.py` | Sınıf düzeyi `asyncio.Event` kanalı; uçuştaki görevler `CancelledError` ile kesiliyor; final durum **koşullu UPDATE** (`cancelled` ezilmiyor); Celery revoke |
| Kimlik doğrulama | `core/security.py`, `main.py` | `SHOWIS_API_TOKEN` (X-API-Token / Bearer, `hmac.compare_digest`), 401 + `WWW-Authenticate`, token yoksa 503; `/health` açık; `/docs`,`/redoc`,`/openapi.json` varsayılan **kapalı** (`SHOWIS_DOCS_ENABLED`) |
| Edge | `infra/caddy/*`, `apps/web/app/api/[...path]/route.ts` | Caddy basic auth, `X-Forwarded-For`/`X-Real-IP` **ezilir**, token sunucu tarafında eklenir; `8005` publish kaldırıldı |
| Audit | `services/audit.py`, `main.py` | `finding.update`, `report.download`, `scan.cancel`, `case.delete/export`, `settings.provider.*`, `auth.failed`, `request.rejected(413)`; `actor_ip` = X-Real-IP → XFF **son** değeri (D-01) |
| SSE | `api/v1/scans.py`, `services/orchestrator.py` | `scan_id` doğrulama (404), tarama başına 3 bağlantı (429), 15 dk üst sınır, 20 sn heartbeat, kuyruk `maxsize=500` + en eskiyi düşür |
| Rapor | `showis_engine/report.py`, `services/report_service.py` | Yönetici özeti, platform dağılımı, kimlik/cross-link tablosu, kronoloji, kanıt özeti; **HTML** biçimi + CSP; vaka bazlı klasör `data/reports/<case>/`; Jinja2 autoescape (XSS/SSTI kapalı) |
| Platform kapsamı | `publicapi/{stackoverflow,bitbucket,mastodon,codeberg}.py` | 4 yeni anahtarsız connector (toplam **29**, profil connector'ı 26) |
| Kalibrasyon | `showis_engine/{scan_modes,calibration}.py`, `scripts/calibrate_scan.py` | Gerçek görev sürelerinden süre tahmini (`calibration.json`: 151 örnek), `tasks.started_at` ölçümü |
| Kimlik grafiği | `showis_engine/identity_edges.py`, `scripts/backfill_edges.py` | `SAME_PERSON`/`CROSS_LINK` edge üretimi + geçmiş taramalar için dry-run backfill |
| Authenticity | `showis_engine/authenticity.py` | Sahte/spam hesap sinyalleri (hesap yaşı, aktivite, boş bio, varsayılan avatar, cross-link tutarlılığı) resolver skoruna giriyor |
| Site-check | `connectors/sitecheck/{cache,dedupe,engine}.py` | Sonuç önbelleği (`SHOWIS_SITECHECK_CACHE_TTL`), tekrar eden kullanıcı adı dedupe |
| Güvenlik sertleştirme | `main.py`, `services/audit.py` | Gövde sınırı 8 MB → 413 (`SHOWIS_MAX_BODY_BYTES`), CORS allowlist, yasaklı host/SSRF politikası, log redaksiyonu |
| Ops | `infra/scripts/{backup,backup-verify,restore,ensure-perms}.sh`, `ci.yml` | Yedek + restore tatbikatı (700/600 izinler), digest'li imajlar, CI'da pip-audit + npm audit + secret tarama |

## Doğrulama

```bash
make test                      # 406+ test (pytest)
make lint                      # ruff
make typecheck                 # mypy
cd apps/web && npm run build   # tip kontrolü + build

# canlı (Caddy tek giriş noktası, basic auth + token):
SHOWIS_API_TOKEN=*** SHOWIS_BASIC_AUTH_USER=*** SHOWIS_BASIC_AUTH_PASSWORD=*** \
  bash scripts/e2e_acceptance.sh          # uçtan uca kabul
bash scripts/smoke_scan.sh --mode O2      # süre ölçümü + DB kanıtı
```

Ölçülen referans (canlı, O2): **337 sn / 140 görev / 906 istek / 138 bulgu**;
tahmin ucu 305 sn (±%11). Sıralı yürütme referansı 636 sn idi.

## Açık işler (öncelik sırası)

1. **Dağıtım (bloklayıcı).** T-302/T-304/T-313/T-316/T-320 imajları **canlıya alınmadı**
   (`docker compose up -d --no-deps api web worker` Mert'te). SEC-13 (`auth.failed` +
   `actor_ip`) ve SEC-16 (404 + 3 bağlantı limiti) canlıda doğrulanmadan final teslim
   için NO-GO. Retest: `GET /api/v1/scans/<yok>/events` → 404, 4. bağlantı → 429,
   `audit_logs`'ta `auth.failed` + `details.actor_ip`.
2. **npm audit / `postcss` (next 16 yükseltmesi).** `next@16.3.x` içindeki gömülü
   `postcss<=8.5.22` high advisory (4 GHSA) Mert'in CI allowlist'inde. `next@16.4.6`
   (breaking) yükseltmesi sonrası allowlist **silinmeli**, kapı blocking kalmalı.
   Ayrıca `caddy:2.7.x` sürüm yükseltmesi (digest'li ama eski).
3. **Çok kiracılı sahiplik (IDOR'a hazırlık).** Tek kullanıcı/paylaşılan token modeli
   var; `owner_id` kolonu + her sorguda sahiplik filtresi (404 dönüşü) eklenmeden
   çok kiracıya geçilmemeli. `cases/findings/settings` uçları bugün yalnız auth'lu.
4. **SEC-10 — `guard_query` hâlâ yalnız LLM yolunda.** `planner.build_template_fallback_queries`
   hint'ten üretilen sorguları guard'dan geçirmiyor (`hints` allowlist'i var ama `extra`
   alanına yasak desen yazılırsa şablon sorgusu denetimsiz çıkar). Düzeltme:
   şablon sorgularını da `guard_query`'den geçir + `policy.query_blocked` audit'i.
5. **`settings.py` audit'lerinde `actor_ip` yok.** `settings.provider.update/delete`
   `record_audit(...)` kullanıyor; `record_request_audit(db, request, ...)`'e geçiş
   (tek satır × 2) — sır rotasyonu izinin IP'si eksik kalıyor.
6. **`edge`'ler için canlı backfill onayı.** `scripts/backfill_edges.py` hazır
   (varsayılan dry-run). Canlı DB'ye yazma Atlas onayı bekliyor; dry-run raporu
   `outbox/software-architect.md` (T-309) içinde.
7. **SEC-19 — host `data/reports/` eski kopyaları.** Konteyner artık docker volume
   kullanıyor ama host'ta eski (PII içeren) PDF/JSON dosyaları dünya-okunur durumda;
   `chmod 700/600` + KVKK saklama taraması (Mert).
8. **SEC-20 — `storage_key` istemciye dönüyor** (`findings.py`: evidence yanıtı).
   Yalnız dosya adı/`evidence_id` dönmeli (düşük, tek satır).
9. **Planner çıktısı connector sorgularına girmiyor** (eski madde 5): `QueryPlanner.plan()`
   sonuçları loglanıyor; LLM varsa buradan gelen sorgular `WebSearchConnector`'a beslenmeli.
10. **Çoklu dil + docx/xlsx + evidence gallery** (ROADMAP 5.2 kalanı) ve
    `case.export`'un `GET` yerine `POST`'a taşınması (yan etkili okuma).

## Dokunmadan önce sor

- `packages/contracts/showis_contracts/enums.py` (`FindingStatus`, `Platform`) ve
  resolver/authenticity skor ağırlıkları: API/DB şeması ve tüm testler bağlı.
- Tier politikası (`showis_engine/policy.py`): Tier C connector yalnız arama görevi
  üretebilir — güvenlik sınırı.
- `.env.example` / `infra/.env.example` yalnız **anahtar adı** taşır; değer yazmayın.
- `apps/web/lib/types.ts` şema jeneratöründen üretilir (`make schemas`); `Platform`
  enum'una ekleme yaptıysanız jeneratörü çalıştırıp farkı commit edin.
