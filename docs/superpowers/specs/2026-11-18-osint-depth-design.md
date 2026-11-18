# Tasarım — OSINT derinliği: kapsama, e-posta pivotu, skor sertleştirme

- **Tarih:** 2026-11-18
- **Durum:** uygulanıyor
- **Sıra:** A (kapsama) → B (e-posta pivotu) → C (skor)

## Neden bu üç iş

Realist fark analizi: Showis 86 uçla "savunulabilir tek vaka" liginde, Sherlock/Maigret
sınıfının 400-3000 site kapsamasının gerisinde. Kalibrasyon dosyası yalnız **süre**
kalibrasyonu; skorda istatistiksel taban yok ve tek sinyalle CONFIRMED mümkün.

## A. Kapsama — 64 → 600+ site (CC BY-SA veri)

**Kaynak:** WhatsMyName `wmn-data.json` (691 site, CC BY-SA 4.0, arşivli ama kalıcı).
Kopyalamıyoruz: **dönüştürüyoruz** ve veri dosyasını BY-SA olarak işaretliyoruz.

**Dönüşüm kuralları** (`scripts/import_wmn_sites.py`):

| WMN | Bizim SiteSpec |
|---|---|
| `uri_check` (`{account}`) | `url_template` (`{u}`) |
| `e_code` | `expect_status` |
| `e_string` | `present_regex` (**`re.escape`** — WMN literal substring) |
| `m_string` | `absent_regex` (escape) |
| `m_code` | `absent_statuses` |
| `post_body` | **dışlanır** (motor GET-only) |
| `valid: false` | dışlanır |
| `protection: cloudflare/captcha/ddos-guard/anubis` | dışlanır (bypass yok) |
| `cat` | `platform = WEB` (WMN kategorisi platform değil) |

**Lisans uyumu:** `sitecheck/data/wmn-sites.json` içinde `_license` bloğu (copyright +
CC BY-SA 4.0 + kaynak URL + "adapted"), yanında `NOTICE.md`, repo kökünde
`LICENSES/CC-BY-SA-4.0.txt`. Kod MIT kalır; **türetilmiş veri** BY-SA.

**Bütçe sorunu (kritik):** 620 site × 6 kullanıcı adı = 3720 istek → O2'nin 1200 istek
bütçesini tek başına yer. Çözüm:
- ~~Mod başına site tavanı: O1=0, O2=120, O3=220~~ → **A2b/D2 ile değişti:** mod başına
  **istek bütçesi** `{O1:0, O2:600, O3:1200}` (`SHOWIS_SITECHECK_REQUEST_BUDGET`);
  planlanan kullanıcı adı sayısı `n` bilinir → `site_limit = max(1, budget // n)`,
  böylece `n × site_limit ≤ budget`. Sitecheck planı en fazla **10** kullanıcı adı üretir.
- Siteler kullanıcı adları arasında **round-robin** dağıtılır; tek kullanıcı adı tüm
  listeyi yiyip diğerlerini aç bırakmaz.
- Elle yazılmış **64** spec **önce** gelir (daha iyi ayarlı), WMN sonra.
- Aynı host iki kez planlanmaz; `key` çakışırsa elle yazılmış kazanır.

**Kabul:** `wmn-sites.json` ≥ 300 geçerli spec; yükleyici regex'leri derler, `{u}`
varlığını ve https'i doğrular; O2 taraması bütçeyi aşmaz (test); O2'de toplam sitecheck
isteği ≤ 600, O3'te ≤ 1200.

## B. E-posta pivotu

Bugün `Seed.email` var ama (a) UI'da alan yok, (b) yalnız Gravatar hash'liyor,
(c) Keybase e-posta ile aranmıyor.

1. **UI:** seed satırına `E-posta` alanı + payload (`email`).
2. **Keybase:** `user/lookup.json?email=` (API destekli) — e-posta → kullanıcı adı/proof.
3. **GitHub commit araması:** `author-email:` yalnız token varsa; token yoksa görev üretilmez.
4. **Kapsam dışı (gerekçeli):** parola-sıfırlama uçlarıyla hesap yoklama (ToS/etki
   nedeniyle), breach/telefon verisi (veri satın alma işi, kod değil).

**Kabul:** e-posta seed'i UI'dan girilip DB'ye düşer; Gravatar + Keybase e-posta
görevleri üretilir; token yokken GitHub e-posta görevi üretilmez; testler yeşil.

## C. Skor sertleştirme

**Sorun:** `username_exact(0.30) + cross_link(0.25) + bio(0.15) + email(0.15) = 0.85`
→ tek başına doğrulanmamış sinyallerle CONFIRMED.

**Kural (istatistik değil, yapısal):** CONFIRMED için skor ≥ 0.80 **ve** en az bir
**bağımsız doğrulama** sinyali: `cross_link` | `commit_email_or_pgp` | `avatar_exact_hash`.
Bu üçü, hedefin profilinde bizzat gözlemlenen bağlantı/kanıt türleridir; `username` ve
`bio` benzerliği tek başına kimlik kanıtı sayılmaz.

- `Finding.confidence_basis: str` ("cross_link", "commit_email", "avatar", "weak") —
  rapor ve UI'da gösterilir, "neden CONFIRMED" sorusunu yanıtlar.
- Eşik 0.80 ve CANDIDATE 0.50 değişmez; yalnız CONFIRMED'a ek koşul gelir.
- Name-mode skorları aynı kalır; yalnız statü dağılımı değişir (bir kısım bulgu
  CANDIDATE'e iner) — bu **istenen** davranış.

**Kabul:** tek `username_exact + bio` ile CONFIRMED yok (test); `cross_link` varken
CONFIRMED korunur; mevcut resolver testleri yeşil; rapor/UI `confidence_basis` gösterir.

## Riskler

| Risk | Azaltma |
|---|---|
| WMN verisi eskimiş (arşiv) | `valid`/`protection` filtresi + sonuçlar "lead" olarak sunulur; rapor notu |
| 620 site → yanlış pozitif | Tespit yalnız pozitif sinyalle "var"; `unknown` varsayılan |
| Bütçe patlaması | Mod tavanı + round-robin + mevcut bütçe kapısı |
| Lisans ihlali | BY-SA veri dosyası + NOTICE + kaynak/kategori alanları korunur |
| E-posta ToS ihlali | Yalnız herkese açık API'ler; parola-sıfırlama yoklama yok |

## Dosya sahipliği

- A: `scripts/import_wmn_sites.py`, `sitecheck/data/`, `sitecheck/wmn.py`,
  `sitecheck/engine.py`, `sitecheck/connector.py`, `tests/test_sitecheck*.py`, `LICENSES/`
- B: `apps/web/app/cases/new/page.tsx`, `apps/web/lib/api.ts`,
  `publicapi/keybase.py`, `publicapi/github_email.py`, `tests/test_publicapi_new.py`
- C: `resolver.py`, `contracts/findings.py`, `report.py`, `llm_explainer.py`,
  `panels.tsx`, `tests/test_resolver.py`
