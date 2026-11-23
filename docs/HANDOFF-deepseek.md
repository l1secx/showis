# Handoff — Showis geliştirme notları

Bu not, aynı depoda çalışan asistana devir için yazıldı.
Amaç: **isim + soyisim (+ okul) verildiğinde anlamlı sonuç üretmek**, sonuçları
Bulgular sekmesine akıtmak, kimlik grafiğini kurmak, tarama süresini önceden
tahmin etmek ve raporu tek sayfada karar verilebilir hale getirmek.

> **Son güncelleme:** 2026-11-17 sabah (gece kampanyası sonu, **70+ görev**, T-101 → T-372).
> Bu sürüm **ikinci fazı** (03:00 sonrası: vaka arama/sayfalama, 64 site-check, sorgu
> indeksleri, authenticity backfill, HTML rapor, SSE kirası, LLM devre kesici) ve güncel
> açık işleri içerir. Kısa özet: `.orchestrator/final-report.md`.

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

### İkinci faz (03:00 sonrası)

| Alan | Dosya / konu | Değişiklik |
| --- | --- | --- |
| Vaka listesi | `api/v1/cases.py` (T-358, T-366) | `GET /cases?q=&status=&mode=&limit=&offset=&sort=` → zarf (`items/total/limit/offset`); `%`/`_` kaçışlı ILIKE, N+1 yok (≤6 sorgu); `legacy=1` (≤500 satır) web uyumu; `q≤128`, `offset≤10 000` |
| Site-check | `connectors/sitecheck/sites.py`, `scripts/validate_sites.py` (T-357) | **48 → 64 spec** (+29 `Platform` üyesi); 12 canlı doğrulandı, yanlış pozitif 0; `{u}` enjeksiyonu `quote(safe="")` + allowlist ile kapatıldı |
| Veritabanı | `alembic/versions/0006_query_indexes.py` | **11 indeks** (cases/scans/targets/findings); 100k satırda Q1 50×, Q4 357× hızlı, FK kaskad 3.3× |
| Authenticity | `showis_engine/authenticity.py`, `scanner.py`, `scripts/backfill_authenticity.py` (T-326/T-367) | Skor artık `findings.authenticity` + `reasons` olarak yazılıyor; geçmiş 137 bulgu backfill edildi (yalnız NULL, kararlar korunur); API `?min_authenticity` |
| Kimlik id'leri | `showis_contracts/entities.py` (T-346) | `make_entity_id` / `make_finding_id`: 96 karakter sınırı, uzun kimlikte deterministik hash (HTB UUID taşması kapandı) |
| HTML rapor | `showis_engine/report.py`, `api/v1/reports.py` (T-313/T-316) | `format=html` + `CSP: default-src 'none'`; vaka bazlı klasör; `X-Content-Type-Options` |
| SSE | `api/v1/scans.py` (T-302/T-354/T-356) | Kira (lease) tabanlı slot: `lease_id`/`renewed_at`, 90 sn TTL reap, `is_disconnected()` kontrolü, `Retry-After`, `X-Scan-Status` |
| LLM | `packages/llm/showis_llm/breaker.py` (T-352) | Devre kesici: ardışık hata sonrası kısa süre LLM atlanır, yerel açıklamaya düşer |
| Audit | `services/audit.py`, `main.py` (T-320/T-343/T-356) | `actor_ip` güven modeli (X-Real-IP → XFF **sağdan ilk güvenilmeyen**, peer CIDR kontrolü `SHOWIS_TRUSTED_PROXIES`); 401/413 audit'i **hız sınırlı** (5/dk, 503 → 1/dk, `suppressed` özeti) |
| Dayanıklılık | `tests/test_scanner_soak.py` (T-331/T-345) | 100 görev/c=8, %20 hata, iptal fırtınası, bütçe senaryoları; deterministik Event tabanlı senkronizasyon, 20/20 koşu flake=0 |

## Nasıl doğrularım

| Komut | Ne yapar | Gerekli env / dosya |
| --- | --- | --- |
| `make gate` | Statik kapı: ruff + mypy + pytest + web build (**canlıya dokunmaz**) | — |
| `bash scripts/final_gate.sh --live` | Yukarısı + uçtan uca kabul testi (`--strict`) | `SHOWIS_API_TOKEN`, `SHOWIS_BASIC_AUTH_USER/PASSWORD` (veya `--basic-auth user:parola`) |
| `bash scripts/e2e_acceptance.sh --strict` | vaka → tahmin → tarama → bulgu → rapor → export → silme akışı | aynı ikili (token + edge parolası) |
| `bash scripts/smoke_scan.sh --mode O2` | Canlı tarama süresi + DB kanıtı (`data/smoke/*.json`) | `--basic-auth` veya `SHOWIS_BASIC_AUTH_*` + token |
| `python scripts/backfill_edges.py --case-id <uuid> --dry-run` | Kimlik edge'lerini geçmiş taramalar için üretir (varsayılan dry-run) | DSN: `--database-url` → `SHOWIS_DATABASE_URL` → `DATABASE_URL` → `infra/.env`; `--apply` için **Atlas onayı** |
| `python scripts/backfill_authenticity.py --dry-run` | NULL `authenticity` alanlarını doldurur (idempotent, kararlar korunur) | aynı DSN; `--apply` yazma yapar |
| `python scripts/validate_sites.py [--static\|--live --limit 15]` | Site-check spec doğrulaması; `--static` ağsız, `--live` gerçek istek | ağ yalnız `--live` |
| `bash infra/scripts/auth-smoke.sh` | Edge basic auth + API token + `8005` kapalı kontrolü | parola: `SHOWIS_CADDY_PASSWORD_FILE` (varsayılan `infra/.secrets/caddy-basic-auth.txt`) |
| `bash infra/scripts/backup-verify.sh` | Yedeği sha256 ile doğrular, `showis_restore_test`'e restore eder (**canlı DB'ye dokunmaz**) | `infra/.env` (POSTGRES_*) |

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

> Durum: yeni imajlar **canlıda** (doğrulandı: `GET /cases` zarf döner, `/scans/<yok>/events` → 404,
> `audit_logs`'ta `auth.failed` / `finding.update` / `report.download` / `request.rejected` var).
> Kalan işler aşağıda; her satırda **neden → nerede → tahmini efor**.

| # | Açık | Neden | Nerede | Efor |
| --- | --- | --- | --- | --- |
| 1 | **npm audit `postcss` (next 16 major)** | `next@16.3.x` içindeki gömülü `postcss<=8.5.22` **high** advisory (4 GHSA) CI allowlist'inde; yükseltme yapılmadıkça kapı gerçekten "blocking" değil | `apps/web/package.json`, `.github/workflows/ci.yml` (allowlist), `infra/docker/Dockerfile.web` | 0.5–1 gün (breaking upgrade + rebuild + smoke) |
| 2 | **Çok kiracılı sahiplik (IDOR hazırlığı)** | Tek paylaşılan token: kimliği olan herkes **tüm** vakaları/bulguları görür. Çok kullanıcıya geçildiği an IDOR olur | `apps/api/showis/db/models.py` (`owner_id` + migration), `api/v1/{cases,scans,findings,reports,settings}.py` (her sorguya sahiplik filtresi, 404) | 2–3 gün |
| 3 | **Edge (Caddy) basic-auth 401'leri audit edilmiyor** | Yanlış basic parola API'ye hiç ulaşmıyor → `auth.failed` yazılmıyor; brute-force yalnız Caddy erişim logunda | `infra/caddy/Caddyfile` + `infra/scripts/caddy-render-config.sh` (erişim logu/rate-limit ya da log→API köprüsü) | 0.5 gün |
| 4 | **`settings.py` audit'lerinde `actor_ip` yok** | `settings.provider.update/delete` `record_audit(...)` kullanıyor; sır rotasyonunun IP'si kayıtsız (canlıda `settings.provider.*` satırı da yok) | `apps/api/showis/api/v1/settings.py:209,233` → `record_request_audit(db, request, ...)` | 10 dakika |
| 5 | **`guard_query` yalnız LLM yolunda** | `build_template_fallback_queries` hint'ten ürettiği sorguları guard'dan geçirmiyor; `extra` alanına yasak desen yazılırsa şablon sorgusu denetimsiz çıkar | `showis_engine/planner.py` (şablon üretimi) + `policy.guard_query` + `policy.query_blocked` audit'i | 0.5 gün |
| 6 | **`pg_trgm` GIN / normalize arama kolonu** | `ILIKE '%q%'` ölçekte seq scan (şu an 100 vaka sorun değil, 10k'da değil); Türkçe **büyük `İ`** katlaması SQL'de eksik (`q=inceleme` ≠ `İnceleme…`) | Migration (`pg_trgm` + `GIN (name gin_trgm_ops)`), `cases.name`/`targets` ad-soyad; kalıcı çözüm `search_name` kolonu | 0.5 gün (+ Atlas onayı) |
| 7 | **MinIO evidence entegrasyonu (ROADMAP 4.2)** | Kanıt dosyaları konteyner diskinde; S3 lifecycle/ölçekleme yok, `storage_key` yerel yol | `showis_engine/http.py:EvidenceStore`, `services/retention.py`, `infra/docker-compose.yml` (minio zaten var) | 2–3 gün |
| 8 | **LUKS** | TLS `:8443` internal CA ile açık; HTTP `:8083` yalnız loopback. Disk hâlâ şifresiz → cihaz kaybı = PII ifşası | host `cryptsetup` | LUKS bakım penceresi (yeniden kurulum) |

Küçük/tek satırlık kalanlar: `case.export` hâlâ `GET` (yan etkili okuma → `POST`),
`storage_key` istemciye dönüyor (SEC-20, yalnız dosya adı dönmeli),
planner çıktısı connector sorgularına beslenmiyor, ROADMAP 5.2 kalanı (çoklu dil,
word cloud, docx/xlsx).

## Dokunmadan önce sor

- `packages/contracts/showis_contracts/enums.py` (`FindingStatus`, `Platform`) ve
  resolver/authenticity skor ağırlıkları: API/DB şeması ve tüm testler bağlı.
- Tier politikası (`showis_engine/policy.py`): Tier C connector yalnız arama görevi
  üretebilir — güvenlik sınırı.
- `.env.example` / `infra/.env.example` yalnız **anahtar adı** taşır; değer yazmayın.
- `apps/web/lib/types.ts` şema jeneratöründen üretilir (`make schemas`); `Platform`
  enum'una ekleme yaptıysanız jeneratörü çalıştırıp farkı commit edin.
- `SHOWIS_TRUSTED_PROXIES`: `actor_ip` yalnız bu CIDR'lerdeki peer'lar için başlıklara
  güvenir; üretimde daraltın (runbook 7b).
- Migration sırası: `0006_query_indexes` ve `0005_wide_identity_ids` **önce** uygulanmalı,
  sonra yeni imajlar (id genişliği + indeksler kod varsayımı).
- SSE slotları süreç içi: çok replikada limit replika başına 3'tür (paylaşımlı sayaç
  gerekirse Redis).
