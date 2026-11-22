# Showis — OSINT İnceleme Aracı

Ad + soyad (ve ipuçları) ile başlayarak kamuya açık kaynaklarda platform
hesaplarını bulan, kimlik grafiği (cross-link) kuran ve güven skoru ile
raporlayan kişisel OSINT web uygulaması.

> **Yasal çerçeve:** Yalnızca herkese açık sayfalar/API'ler taranır. Giriş
> duvarı, CAPTCHA bypass, unofficial API ve yasak host'lar kodda yoktur.
> Bkz. `docs/legal/`.

## Özellikler

- **26 Zengin Connector (anahtarsız, Tier A/B):** GitHub, GitLab, Keybase, Reddit,
  Medium, LeetCode, Codeforces, **Stack Overflow, Bitbucket, Mastodon (mastodon.social),
  Codeberg**, DockerHub, Dev.to, HackerNews, NPM, PyPI, Chess, Steam, Gravatar, Bluesky,
  HuggingFace, Kitsu, SoundCloud, WordPress, TryHackMe, HackTheBox. Toplam connector
  sayısı (site-check + web arama + render dahil) **29**.
- **64 Platform Site-Check:** Kullanıcı adı varlık doğrulaması (Kaggle, HackerRank,
  Root-Me, Duolingo, Replit, HackerOne, Bugcrowd, Kick, Ko-fi, SourceHut, Velog, vb.) — 64 site, sonuç önbelleği (`SHOWIS_SITECHECK_CACHE_TTL`).
- **Kimlik Grafiği:** `SAME_PERSON` (ortak kullanıcı adı / avatar / e-posta kanıtı) ve `CROSS_LINK` (bio'daki
  platform bağlantıları) edge'leri; `GET /cases/{id}/graph` + Cytoscape.js paneli
  (güven eşiği / platform / edge tipi filtreleri, PNG·JSON·GEXF dışa aktarma).
- **Authenticity (sahte/spam hesap) sinyalleri:** hesap yaşı (`created_at`), aktivite
  (`followers_count`, repo/post sayıları), boş bio, varsayılan avatar ve cross-link
  tutarlılığı skora katkı olarak işlenir (deterministik resolver, 7+ sinyal).
- **Görsel Avatar Eşleme (dHash):** algısal 64-bit fark karması + birebir SHA-256.
- **LLM Dinamik Planlayıcı & Açıklayıcı:** OpenCode Go / OpenAI uyumlu BYOK;
  `SHOWIS_LLM_BASE_URL_ALLOWLIST` ile host allowlist, veri redaksiyonu (`redact_text`),
  şablon fallback.
- **Hibrit Arama:** Brave → SerpAPI → Google CSE → DuckDuckGo fallback zinciri + URL tekilleştirme.
- **Varyant Motoru:** Türkçe karakter normalizasyonu + akıllı kullanıcı adı adayları.
- **Paralel Tarama:** `SHOWIS_SCAN_CONCURRENCY` (varsayılan 4) sınırlı eşzamanlılık;
  host başına rate limit (`SHOWIS_HOST_MAX_CONCURRENCY` / `SHOWIS_HOST_MIN_INTERVAL`);
  bütçe HTTP katmanında fail-closed; iptal gerçekten çalışır (koşullu `UPDATE`, uçuştaki
  görevler `CancelledError` ile kesilir).
- **Canlı Akış:** SSE (tarama başına 3 bağlantı, sınırlı kuyruk, 15 dk üst sınır) —
  görev/olay akışı ve artımlı bulgu yazımı.
- **Rapor (PDF / JSON / HTML):** **yönetici özeti** (hedef, mod, toplamlar, durum
  dağılımı, en yüksek 5 bulgu, güven yorumu), **platform dağılımı**, **kimlik/çapraz
  bağlantı tablosu**, **kronoloji** (hesap oluşturma tarihleri), **kanıt özeti**;
  Jinja2 `autoescape` + CSP ile şablon enjeksiyonu/XSS kapalı.
- **Güvenlik:** API `SHOWIS_API_TOKEN` (X-API-Token / Bearer) zorunlu, edge'de Caddy
  basic auth; CORS allowlist; dokümantasyon uçları varsayılan **kapalı**
  (`SHOWIS_DOCS_ENABLED`); istek gövdesi 8 MB ile sınırlı (413); audit izi
  (`actor_ip` = X-Real-IP → XFF **son** değeri) tüm kritik işlemlerde.
- **Vaka yönetimi:** `GET /cases` sunucu tarafı **arama** (vaka adı, amaç, hedef ad/soyad —
  `%`/`_` kaçışlı), durum/mod filtresi, sıralama (`created_desc|created_asc|name_asc|updated_desc`)
  ve sayfalama (`limit` 1..200, `offset`); yanıt zarfı `items/total/limit/offset`
  (eski düz liste için `?legacy=1`). 100+ vakada UI zorlanmaz.
- **Operasyon:** `/health` (container healthcheck, kimliksiz), `/healthz` (derin kontrol: DB/Redis/
  disk), `/api/v1/metrics` (Prometheus metni, token + yerel kaynak); günlük yedek + restore
  tatbikatı (`infra/scripts/backup.sh` → `backup-verify.sh`), yedek dizini `data/backups` (700/600).
- **Uyumluluk:** KVKK saklama süreleri, kaskad silme (rapor klasörü dahil), JSON/PDF/HTML dışa aktarma.

## Kurulum

### Docker (önerilen)

Gereksinimler: Docker Engine + **Docker Compose v2** (`docker compose` eklentisi;
eski `docker-compose` v1, `depends_on` sağlık koşullarını desteklemez), `make`,
ve token üretimi için host'ta `cryptography` paketli bir Python 3
(`pip install cryptography` veya dağıtım paketi — MASTER_KEY üretimi bunu ister).
Caddy basic-auth hash'i için host'ta caddy yoksa:
`docker run --rm caddy:2.7-alpine caddy hash-password --plaintext 'PAROLA'`.

```bash
cp .env.example .env
# Zorunlu: API paylaşılan token'ı. ÖNEMLİ: mevcut SHOWIS_API_TOKEN= satırını
# DÜZENLEYİN (append etmeyin — aksi halde anahtar iki kez tanımlanır).
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # değeri .env içindeki SHOWIS_API_TOKEN='a yazın
# Zorunlu: BYOK sırlarını şifreleyen anahtar — yine .env içindeki MASTER_KEY= satırına
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

make docker-up     # tüm servisler
docker compose -f infra/docker-compose.yml ps
```

**Tek giriş noktası: `http://localhost:8083`** (Caddy). `8005` artık host'a publish
**edilmez**; API'ye yalnız Caddy üzerinden erişilir.

Kimlik doğrulama iki katmanlı:

1. **Edge (Caddy basic auth):** `SHOWIS_CADDY_USER` + `SHOWIS_CADDY_PASSWORD_HASH`
   (`infra/.env`). Hash üretimi: `caddy hash-password --plaintext 'PAROLA'`.
   Parolanın kendisi `infra/.secrets/caddy-basic-auth.txt` (0600) gibi bir dosyada
   tutulur ve script'lere `SHOWIS_CADDY_PASSWORD_FILE` / `--basic-auth` ile verilir —
   **repoya ve rapora yazılmaz**.
2. **API (`SHOWIS_API_TOKEN`):** `X-API-Token: <token>` veya `Authorization: Bearer <token>`.
   Token yok/yanlış → `401` (+ `WWW-Authenticate`); sunucuda token tanımlı değilse API
   fail-closed (`503`). `/health` her zaman açıktır (container healthcheck).

### Geliştirme (docker'sız)

Gereksinim: **Python ≥ 3.12** (Debian 12 = 3.11 → native yol bloklu; docker yolunu
kullanın veya python3.12 kurun) + Node 18+ (`make dev` için).

```bash
# Tek komut bootstrap: .venv + tüm bağımlılıklar + yerel showis-* paketleri
make bootstrap
# Postgres + Redis gerekli — portları host'a açmak için:
docker compose -f infra/docker-compose.yml -f infra/docker-compose.dev.yml up -d postgres redis
make dev           # api :8000 + web :3000
make test          # pytest
make lint          # ruff
make typecheck     # mypy
make schemas       # JSON Schema export
make eval          # eval seti precision@3 raporu
make web-build     # web üretim derlemesi (apps/web)
```

`make dev` (→ `scripts/dev.sh`) kök `.env` ile `infra/.env` dosyalarını okur, boş
değerleri atlar ve docker kimlik bilgilerini `localhost` adresine bağlar
(`DATABASE_URL`, `REDIS_URL`, `SHOWIS_API_TOKEN`). Postgres/Redis host'a açık
değilse yığın izi yerine çalıştırılacak tek komutu yazar; iki süreçten biri
kapanınca diğerini de kapatır (portta öksüz süreç kalmaz).

Kök dizinde `package.json` **yoktur** — monorepo kökünde `npm run build`
çalışmaz. Web derlemesi için `make web-build` veya `cd apps/web && npm run build`.

> **NixOS:** `pip` ile gelen native uzantılar (greenlet → SQLAlchemy asyncio)
> sistem yolunda olmayan `libstdc++.so.6` arar. `Makefile` ve `scripts/dev.sh`
> sistem derleyicisinin kütüphane dizinini `LD_LIBRARY_PATH`'e ekler; ek bir şey
> yapmanız gerekmez.

Operasyon betikleri (`infra/scripts/backup.sh` vb.) **bash ≥ 4** ister (ilişkisel
dizi + GNU `date -d`); Alpine host'ta `apk add bash` gerekir.

`SHOWIS_AUTH_DISABLED=1` yalnız test/dev bypass'ıdır — prod'da **asla** set edilmemeli
(set edilirse API `CRITICAL` loglar). Doküman istiyorsanız `SHOWIS_DOCS_ENABLED=1`
(açıkken bile token gerekir).

### Canlı doğrulama script'leri

```bash
# Uçtan uca kabul: vaka → tarama → bulgu → rapor → export → silme
SHOWIS_API_TOKEN=*** SHOWIS_BASIC_AUTH_USER=*** SHOWIS_BASIC_AUTH_PASSWORD=*** \
  bash scripts/e2e_acceptance.sh

# Tarama süresi ölçümü (O2) + DB kanıtı
bash scripts/smoke_scan.sh --mode O2

# Edge kimlik doğrulama smoke'u
bash infra/scripts/auth-smoke.sh

# Grafik edge backfill (varsayılan dry-run; canlı yazma için --apply)
python scripts/backfill_edges.py --case-id <case-id> --dry-run
```

## Performans (canlı ölçüm)

| Mod | Süre | Görev | İstek | Bulgu |
| --- | --- | --- | --- | --- |
| O2 — sıralı yürütme (önce) | 636 sn | ~142 | ~910 | ~41 |
| O2 — paralel `concurrency=4` (sonra) | **337 sn (-47 %)** | 140 | 906 | 138 |

Kalibrasyon: `packages/engine/showis_engine/calibration.json` (151 örnek, 11 tarama;
`scripts/calibrate_scan.py`). `GET /cases/{id}/scan/estimate?mode=O2` tahmini ±%11 içinde.

## Kullanım

1. **Ayarlar** → sağlayıcı anahtarlarını gir (BYOK, maskeli saklanır)
2. **Yeni Vaka** → amaç + hukuki dayanak + hedef ad/soyad + ipuçları + seed'ler
3. **Tarama** → O1/O2/O3 seç, tahmini süreyi gör, canlı akışı izle (iptal edilebilir)
4. **Bulgular** → skor kırılımı + kanıt paneli → Onayla/Reddet (karar yeniden taramada korunur)
5. **Grafik** → entity/edge ağı, filtreler, dışa aktarma
6. **Rapor** → PDF/JSON/HTML üret + indir (yönetici özeti + tablolar + kronoloji)

## Mimari

```
apps/api     FastAPI + SQLAlchemy(async) + Alembic — REST + SSE
apps/web     Next.js 15 + Tailwind — arayüz (X-API-Token'ı sunucu tarafında ekler)
packages/contracts   Pydantic modeller (tek doğruluk kaynağı)
packages/connectors  github, tryhackme, hackthebox, websearch, publicapi/*, sitecheck
packages/engine      http(+bütçe/limiter), policy(SSRF/robots), variants, resolver,
                     planner, identity_edges, browser, report, links, scan_modes
packages/llm         OpenAI-uyumlu istemci + prompt'lar + redaksiyon
packages/settings    Fernet şifreli BYOK
infra/       docker-compose, Dockerfile'lar, caddy, script'ler
```

Detaylar: `docs/runbook.md`, `docs/HANDOFF-deepseek.md`, `docs/legal/`,
spesifikasyon `.opencode/plan/showis-osint-implementation-spec.md`.
