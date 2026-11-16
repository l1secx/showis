# Showis OSINT Aracı — Uçurma Planı

## Faz 1: Temel Fonksiyonel Eksikleri Tamamlama (Tamamlandı ✅)

### 1.1 LLM Entegrasyonu [TAMAMLANDI ✅]
- **Durum**: BYOK ve ortam değişkeni (`OPENCODE_API_KEY`, `OPENAI_API_KEY`) destekli `LLMClient` scanner'a ve arka plan görevlerine bağlandı.
- **Gerçekleştirilenler**:
  - `QueryPlanner` ve `FindingExplainer` scanner servis döngüsüne entegre edildi.
  - LLM mevcutsa akıllı sorgular üretilir ve bulgu açıklamaları LLM tarafından gizlilik redaksiyonu (`redact_text`) yapılarak yazılır.
  - LLM anahtarı olmadığında şablon planlayıcıya ve yerel deterministik açıklayıcıya zarifçe (graceful fallback) düşülür.
- **Test**: Mock ve entegrasyon testleri ile doğrulandı.

### 1.2 Cross-Link Analizi [TAMAMLANDI ✅]
- **Durum**: Biyo ve profil bağlantılarından otomatik çapraz platform ilişkilendirmesi devrede.
- **Gerçekleştirilenler**:
  - 50+ platform için kanonik profil regex'leri ve URL şablonları tamamlandı.
  - Biyo metinleri ve entity niteliklerindeki bağlantılardan otomatik `CROSS_LINK` kenarları (edge) üretilmektedir.
- **Test**: `tests/test_links.py` test paketi devrede.

### 1.3 Avatar Hash & Görsel Eşleme Motoru [TAMAMLANDI ✅]
- **Durum**: Exact SHA-256 + Algısal Fark Karması (Perceptual dHash) entegre edildi.
- **Gerçekleştirilenler**:
  - Profil avatarları taranırken hem SHA-256 hem de 64-bit dHash hesaplanır.
  - Farklı platformlar tarafından yeniden boyutlandırılmış (örn. 200x200 PNG -> 80x80 JPEG) veya sıkıştırılmış avatarlar Hamming mesafesi toleransı (mesafe <= 6) ile tespit edilir.
  - Düz renk ve varsayılan boş avatarlar yanlış pozitif üretmemesi için elenir.
  - Eşleşen avatarlar `SAME_PERSON` kenarı oluşturur ve çözümleyici güven skoruna yansır.
- **Test**: `tests/test_links_dhash.py` ile görsel sıkıştırma ve tolerans testleri tamamlandı.

### 1.4 Entity Resolution Algoritması [TAMAMLANDI ✅]
- **Durum**: 7 sinyalli dinamik güven skoru ve çelişki tespiti devrede.
- **Gerçekleştirilenler**:
  - Türkçe karakter normalizasyonu (`normalize_turkish`) ve ad-soyad alt küme / SequenceMatcher eşlemesi geliştirildi.
  - Hedef lokasyonu ile aday lokasyonu arasındaki coğrafi çelişkiler otomatik tespit edilir (`detect_contradiction`).
  - Güven eşikleri (≥0.80 CONFIRMED, 0.50-0.79 CANDIDATE, <0.50 REJECTED) ile deterministik ve açıklanabilir sınıflandırma.
- **Test**: `tests/test_resolver.py` ile doğrulandı.

---

## Faz 2: Platform Kapsamı Genişletme (İlerleme Halinde 🚀)

### 2.1 Derin & Kamuya Açık Profil Connector'ları [YENİ PLATFORMLAR EKLENDİ ✅]
- **Eklenen Yeni Connector'lar**:
  - **Reddit** (`RedditConnector`): Kullanıcı karma, avatar, biyo, subreddit başlığı ve onay durumu.
  - **Medium** (`MediumConnector`): Kamuya açık RSS profili üzerinden yazar adı, biyo, avatar ve yazılar.
  - **LeetCode** (`LeetCodeConnector`): GraphQL API üzerinden profil, gerçek isim, ülke, yetenekler, GitHub/LinkedIn/Twitter çapraz bağlantıları.
  - **Codeforces** (`CodeforcesConnector`): Resmî API üzerinden ad/soyad, ülke, şehir, kurum, avatar ve yarışma dereceleri.
  - **SoundCloud** (`SoundCloudConnector`): Profil açık meta etiketlerinden (og:title, og:description, og:image) yapımcı/müzisyen profili.
- Toplam kamuya açık profil connector sayısı **22**'ye ulaştı.

### 2.3 Sitecheck Database Genişletme [TAMAMLANDI ✅]
- Veritabanı **52 platforma** çıkarıldı (Kaggle, HackerRank, Root-Me, Duolingo, Hashnode, Bluesky, HuggingFace, Kitsu, WordPress, Last.fm, LeetCode, Codeforces, Replit, Devpost, Patreon, Vimeo, Bandcamp, Dailymotion, Scratch vb.).

---

## Faz 3: Arama Kalitesi & Hibrit Arama (İlerleme Halinde 🚀)

### 3.2 Hybrid Search Provider [TAMAMLANDI ✅]
- **Gerçekleştirilenler**:
  - `HybridSearchProvider` kuruldu: Birden fazla sağlayıcıyı (Brave, SerpAPI, Google CSE, DuckDuckGo) zincirleme fallback mantığıyla yönetir.
  - Kanonik URL normalizasyonu ile yinelenen arama sonuçları temizlenir (deduplication).
- **Test**: `tests/test_hybrid_search.py` ile doğrulandı.

---

## Faz 2: Platform Kapsamı Genişletme (6-8 hafta)

### 2.1 Derin Connector'lar (4-5 hafta)
- **Her connector 1-2 hafta**, sırayla:
  - **Instagram** (scraping - login duvarı zor, önce herkese açık sayfalar)
  - **TikTok** (scraping - bot-duvarı, rate limit)
  - **LinkedIn** (scraping - çok zor, bot-duvarı, CAPTCHA → render gerekebilir)
  - **Facebook** (scraping - çok zor, login duvarı)
  - **Twitter/X** (official API anahtarlı, yoksa scraping)
  - **Telegram** (API/MTProto) — profile + username query
  - **Discord** (API) — username lookup (restricted)
  - **Steam** (zaten var, geliştir)
  - **Spotify** (scraping)
  - **Strava** (API)
  - **Meetup** (API)
  - **Stack Overflow** (API)
  - **Dev.to** (zaten var)
  - **IndieHackers** (scraping)
  - **ProductHunt** (scraping)
  - **AngelList** (scraping)
  - **Crunchbase** (API)
  - **Shodan** (API)
  - **Censys** (API)
- **Test**: Her connector için var/yok test + canlı kullanıcı doğrulama

### 2.2 Rendered Profile Connector Optimizasyonu (1-2 hafta)
- **Mevcut**: TryHackMe, HackTheBox için Playwright var
- **Hedef**: Generic rendered connector oluşturup 10+ platform için kullan
  - Playwright optimizasyonu (headless, cookie persistence) — stealth/anti-bot YOK (AUP)
  - JS render gerektiren platformlar (Instagram, TikTok, LinkedIn)
  - Rate limit handling + session management
  - Selector tabanlı veri çıkarma (flexible schema)
- **Test**: 5 platform için A/B test (HTTP vs render)

### 2.3 Sitecheck Database Genişletme (1-2 hafta)
- **Mevcut**: 52 site
- **Hedef**: 100+ site
  - Aday platform listesi (150+ site)
  - Her aday için canlı var/yok test
  - Regex/JSON spec'leri belirle
  - SiteCheckConnector'a entegre
- **Test**: 100 site için concurrency + accuracy test

---

## Faz 3: Arama Kalitesi & Performans (4-5 hafta)

### 3.1 DuckDuckGo Kalite (1 hafta)
- **Mevcut**: Basik HTML parse, 30 hit
- **Hedef**:
  - Snippet filtering (relevans skor)
  - Duplicate URL removal
  - Domain blacklisting (spam, tracker)
  - Region selector (TR, US, EU)
  - Pagination (ilgili 100+ hit)
- **Test**: 50 query ile recall/precision ölçümü

### 3.2 Hybrid Search (1-2 hafta)
- **Mevcut**: Tek search provider
- **Hedef**: Multi-provider fusion
  - DuckDuckGo + Brave + SerpAPI paralel sorgu
  - Sonuç merge + dedup
  - Provider-kalırlık skorları
  - Fallback chain (DDG → Brave → SerpAPI)
- **Test**: 100 query ile provider karşılaştırması

### 3.3 Concurrency & Rate Limiting (1-2 hafta) [TAMAMLANDI ✅]
- **Mevcut**: ~~Basik semaphore~~ → **T-101/T-102/T-117 (2026-11-17)**: sınırlı paralel
  yürütme (`SHOWIS_SCAN_CONCURRENCY`, varsayılan 4 — O2 636 sn → **337 sn**), host bazlı
  rate limiter (`HostLimiter`: host başına eşzamanlılık + min interval, 429/403
  nezaketi), bütçe HTTP katmanında **fail-closed** (istek atılmadan `BudgetExceededError`),
  site-check sonuç önbelleği. Kalan (opsiyonel): dağıtık Redis atomikleri, priority queue.
- **Hedef**:
  - Domain-bazlı rate limiting (github.com: 60/min, twitter.com: 30/min)
  - Adaptive backoff (exponential jitter)
  - Priority queue (critical tasks)
  - Distributed concurrency (Redis atomics)
  - Health check (slow domain'leri geçici disable)
- **Test**: 1000 concurrent request stress test

---

## Faz 4: Veri Kalitesi & Entity Management (4-5 hafta)

### 4.1 Fake/Spam Detection (2-3 hafta) [TAMAMLANDI ✅]
- **Durum**: **T-301** (`showis_engine/authenticity.py` + resolver entegrasyonu):
  hesap yaşı (`created_at`), aktivite (`followers_count`, repo/post), boş bio,
  varsayılan avatar, cross-link tutarlılığı tek "authenticity" skorunda birleşiyor;
  `breakdown`'da sinyal bazında görünür.
- **Hedef**: Bot/Sybil hesap filtreleme
  - Creation date check (çok yeni hesaplar düşük confidence)
  - Activity check (0 repository, 0 post = düşük skor)
  - Avatar default check (random avatar = düşük)
  - Bio empty check
  - Cross-link consistency (anomaly detection)
  - Composite "authenticity score"
- **Test**: 1000+ bilinen fake vs gerçek profil sınıflandırması

### 4.2 Evidence Management (1-2 hafta)
- **Mevcut**: JSON evidence, hash saklanıyor
- **Hedef**:
  - MinIO/S3 entegrasyonu (büyük dosyalar)
  - Evidence lifecycle otomasyonu (retention based on case policy)
  - Evidence dedup (hash tabanlı)
  - Evidence search (keyword + URL)
  - Evidence preview (thumbnail)
- **Test**: 10,000 evidence kaydı performans test

### 4.3 Audit Trail & Security (1 hafta) [KISMEN ✅ — 2026-11-17]
- **Durum**: `finding.update`, `report.download`, `scan.cancel`, `case.delete`,
  `case.export`, `scan.start/completed`, `settings.provider.*`, `auth.failed` ve
  `request.rejected(413)` audit'e yazılıyor; `details.actor_ip` =
  `X-Real-IP` → XFF **son** değeri (D-01). **Kalan:** per-user rate limit,
  IP allow/block list, anomali uyarıları; `settings.py` audit'lerinde `actor_ip` eksik.
- **Mevcut**: Audit log var
- **Hedef**:
  - Kişisel veri mask (GDPR: email, IP redaction)
  - Aksiyon logları tam kapsam (who, when, what, ip)
  - Uyarı sistemi (anomali: çok fazla vaka, çok scan)
  - Rate limit per user
  - IP whitelist/blacklist
- **Test**: Audit log export + GDPR delete test

---

## Faz 5: Kullanıcı Deneyimi & Arayüz (4-6 hafta)

### 5.1 Grafik Geliştirmeleri (2-3 hafta) [TAMAMLANDI ✅]
- **Durum (T-305/T-306)**: force/circle/grid yerleşim, platform renkleri + açık kenarlık,
  güven eşiği / platform / edge tipi filtreleri (sessionStorage kalıcı), entity detay
  side panel (Esc ile kapanır, `javascript:` URL engelli), edge tooltip (güven + gerekçe),
  PNG / JSON / GEXF dışa aktarma. 500 düğüm/524 kenar: ilk yükleme ~1.5 sn,
  platform filtresi ~120 ms, PNG ~193 ms.
- **Mevcut**: Cytoscape.js entity/edge
- **Hedef**:
  - Layout algoritmaları (force, circular, hierarchical)
  - Platform bazlı renk şemaları
  - Cluster detection (same person grupları)
  - Interactive filters (confidence threshold, platform)
  - Entity detay side panel
  - Edge tooltip (confidence, reason)
  - Export graph (PNG, SVG, GEXF)
- **Test**: 500 node/edge performans

### 5.2 Report Geliştirmeleri (1-2 hafta) [KISMEN ✅ — 2026-11-17]
- **Durum**: **T-313/T-316** — executive summary (1 sayfa), platform dağılımı,
  cross-link/kimlik tablosu, **timeline** (hesap oluşturma kronolojisi), kanıt özeti,
  HTML biçimi (CSP'li), vaka bazlı rapor klasörü; Jinja2 `autoescape` ile XSS/SSTI
  kapalı. **Kalan:** çoklu dil (EN/DE/FR), evidence gallery (thumbnail), word cloud,
  docx/xlsx.
- **Mevcut**: Türkçe PDF + JSON + HTML
- **Hedef**:
  - Çoklu dil (EN, TR, DE, FR)
  - Executive summary (1-page)
  - Evidence gallery (thumbnail grid)
  - Cross-link tablosu
  - Timeline (activity chronology)
  - Word cloud (bio keywords)
  - Export docx + xlsx
- **Test**: 20 gerçek vaka ile rapor kalite değerlendirmesi

### 5.3 Search History & Templates (1 hafta)
- **Hedef**:
  - Kullanıcı bazlı saved searches
  - Template cases (pre-built queries: "CTF player", "pentester", "developer")
  - Quick replay (tek tıkla yeniden tarama)
  - Public template library (community)
- **Test**: 50 saved search senaryosu

---

## Faz 6: Deployment & Ops (3-4 hafta)

### 6.1 CI/CD (1 hafta)
- **Hedef**:
  - GitHub Actions workflow (test → lint → build → deploy)
  - Docker image optimizasyonu (multi-stage, cache)
  - Database migration automation
  - Canary deployment (blue/green)
- **Test**: 10 commit cycle

### 6.2 Monitoring & Alerting (1-2 hafta)
- **Hedef**:
  - Prometheus + Grafana stack
  - Metrics: request latency, error rate, connector success rates
  - Alerting: PagerDuty/Slack (scan fail, DB down)
  - Log aggregation (Loki/ELK)
  - Health check endpoint (/healthz)
- **Test**: 50 simulated failure senaryosu

### 6.3 Backup & Disaster Recovery (1 hafta) [TAMAMLANDI ✅]
- **Durum (T-304)**: `infra/scripts/backup.sh` (pg_dump + evidence tar, sha256, retention
  gunluk/haftalik, `data/backups` **700/600**), `backup-verify.sh` (dump bütünlüğü),
  `restore.sh` (varsayılan `--to-test`, canlı için `--force-live` + onay,
  `--exit-on-error --single-transaction`), systemd timer ile günlük koşu.
  **Restore tatbikatı yapıldı**: `showis_restore_test`'e yüklendi, 7 tablonun satır
  sayıları canlı DB ile birebir.
- **Hedef**:
  - Automated DB backups (hourly snapshot, 7-day retention)
  - Evidence backup (S3 lifecycle)
  - Restore (DR test)
  - Multi-region failover (opsiyonel)
- **Test**: 1 haftalık veri restore test

---

## Faz 7: Dokümantasyon & Topluluk (2-3 hafta)

### 7.1 Dokümantasyon (1-2 hafta)
- **Hedef**:
  - Kullanıcı kılavuzu (TR + EN)
  - Geliştirici dokümantasyonı (connector ekleme rehberi)
  - API documentation (OpenAPI)
  - Architecture diagram (C4 model)
  - Security whitepaper (legal framework)
- **Test**: Onboarding yeni geliştirici 1 gün içinde çalışır?

### 7.2 Örnek Cases & Eval Set (1 hafta)
- **Hedef**:
  - 100+ gerçek vaka dataset (anonimized)
  - Ground truth entity mapping
  - Precision@3 benchmark
  - Public leaderboard (opsiyonel)
- **Test**: Eval set ile connector performans karşılaştırması

---

## Tahmini Zaman Çizelgesi

| Faz | Süre | Key Deliverables |
|-----|------|-------------------|
| 1 | 4-6 hafta | LLM entegrasyonu, cross-link, avatar hash, entity resolution |
| 2 | 6-8 hafta | 100+ sitecheck site, 20+ derin connector, rendered optimizasyon |
| 3 | 4-5 hafta | Hybrid search, concurrency, rate limiting |
| 4 | 4-5 hafta | Fake detection, evidence management, audit trail |
| 5 | 4-6 hafta | Grafik geliştirmeleri, çoklu dil rapor, saved searches |
| 6 | 3-4 hafta | CI/CD, monitoring, backup/DR |
| 7 | 2-3 hafta | Dokümantasyon, eval set |
| **Toplam** | **27-37 hafta** | **Production-ready, enterprise-grade OSINT aracı** |

---

## Riskler & Mitigasyon

| Risk | Olasılık | Etki | Mitigasyon |
|------|----------|-----|------------|
| LinkedIn/Instagram bot-duvarı | Yüksek | Orta | Render connector; bot koruması aşma (stealth/proxy) politika gereği kapsam dışı |
| API key maliyeti | Orta | Orta | DuckDuckGo fallback + rate limiting + budget cap |
| Performans düşüşü (1000+ request) | Orta | Yüksek | Concurrency limiter + queue + distributed lock |
| Legal compliance (GDPR) | Düşük | Yüksek | Legal review + retention otomasyonu + data minimization |
| Maintainer burnout | Orta | Yüksek | Modüler mimari + test coverage + dokümantasyon |

---

## Başarı Kriterleri

- **Kapsam**: 100+ platform, 50+ derin connector
- **Kalite**: Precision@3 ≥ 0.80 (eval set)
- **Performans**: 1000 HTTP request, <5 dakika
- **Uptime**: 99.5% (monitoring ile)
- **Dokümantasyon**: Tüm API'ler + connector ekleme rehberi
- **Test**: %90+ code coverage

Bu plan, 6-9 ayda Showis'i **enterprise-grade, multi-platform, LLM-augmented OSINT aracı** haline getirir.
