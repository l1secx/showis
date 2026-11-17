# Tasarım — Handle-first hedef modeli (nick ile arama, soyad opsiyonel)

- **Tarih:** 2026-11-17
- **Durum:** onay bekliyor
- **Kapsam:** `packages/contracts`, `packages/engine`, **`packages/connectors`**, `apps/api`,
  `apps/web` (+ `types.generated.ts`), migration `0007`
- **Teslim sırası (karar):** **Faz 1** = kimlik modeli + connector'lar + planner düzeltmesi
  (§6.4.1, §6.4.2). **Faz 2** = planner'ın gerçekten çalıştırılması (§6.4.3).
  Faz 1 `make gate` yeşil olmadan Faz 2'ye geçilmez. Ayrı commit'ler.
- **Seçilen yaklaşım:** B — iki giriş modu ("İsimle ara" / "Kullanıcı adıyla ara")

## 1. Amaç ve neden

Bugün Showis isim-first: `TargetInput.first_name` ve `last_name` ikisi de zorunlu
(`apps/api/showis/api/v1/cases.py:106-114`), DB'de `nullable=False`
(`apps/api/showis/db/models.py:90-91`), formda ikisi de `required`
(`apps/web/app/cases/new/page.tsx:123-142`).

Kullanıcı yalnızca bir nick/kullanıcı adı biliyorsa vaka açamıyor. Oysa kaliteli OSINT
araçları (Sherlock, Maigret, WhatsMyName) **handle-first** çalışır: elimizdeki tek şey
kullanıcı adıdır, isim sonradan doğrulama sinyali olur. Showis'in altyapısı buna zaten
yarı hazır: `generate_variants` `hints["nickname"] or hints["username"]` okuyup nick
varyantları üretiyor (`packages/engine/showis_engine/variants.py:52-74`), ama model,
API ve arayüz bu yolu dışlıyor.

## 2. Hedefler

1. Vaka **yalnızca nick** ile açılıp taranabilsin; soyad hiçbir yerde zorunlu olmasın.
2. İki giriş modu, davranışta **gerçek** fark yaratsın (kozmetik segment değil):
   handle modunda sorgular, varyantlar, skorlama ve açıklamalar handle-first olur.
3. İsim modunda nick opsiyonel **takviye** sinyali olarak kullanılabilsin.
4. Mevcut vakalar, mevcut API istemcileri ve mevcut testler kırılmasın.
5. Handle modunda kapsama **gerçek** olsun: connector'lar da handle ile görev üretsin,
   boş isimden bozuk sorgu (`"" in:name`) çıkmasın, web araması sessizce kaybolmasın.
6. Planner'ın ürettiği platforma özel sorgular çöpe gitmesin; tavanlı ve tekilleştirilmiş
   şekilde gerçek arama görevine dönüşsün (Faz 2).

## 3. Hedef olmayanlar

- Nick **tahmini** (sözlükten alternatif nick üretme: `shadow`, `reis`, `bozkurt`…).
  `docs/en_nicknames.json` ve `docs/tr_nicknames.json` kodda hiç kullanılmıyor
  (doğrulandı: `grep -rn "nicknames.json"` → 0 sonuç) ve kullanılmayacak: 20 kelimelik
  bir liste yanlış-pozitif üretir. Sherlock de tahmin etmez, verilen handle'ı dener.
- Leet/typo varyant üretimi (sonraki iş).
- E-posta-only hedef modu (ayrı tasarım).
- Seed yapısının değişmesi.

## 4. Anti-hedefler

- Skor eşiklerini (0.80 CONFIRMED / 0.50 CANDIDATE) gevşetmek. Handle modu daha az
  sinyal taşır; bu, skoru şişirerek değil, **hangi sinyalin yok olduğunu dürüstçe
  göstermekle** ele alınır.
- `hints` JSON'una yeni bir "yarı-resmi" kimlik alanı eklemek. Nick artık birinci sınıf
  kimlik; JSON ipucu olarak kalmamalı.

## 5. Yaklaşımlar (karar)

| | Yaklaşım | Artı | Eksi | Karar |
|---|---|---|---|---|
| A | Esnek tek form, ad/soyad nullable + nick alanı | 1/3 efor | Ad ile nick aynı kutuda karışır; "kaliteli tool" hissi yok | reddedildi |
| **B** | **İki mod: İsim / Handle** | Zihinsel model net, gerçek davranış farkı, Sherlock/Maigret mantığı | Orta efor (model+API+engine+UI+rapor+test) | **seçildi** |
| C | Seed-first (kimlik tamamen opsiyonel) | En esnek | Skorlama/eşleştirme baştan yazılır, yüksek regresyon | reddedildi |

B, A'nın tüm küçük düzeltmelerini içerir (soyad opsiyonelliği oradan geliyor).

## 6. Tasarım

### 6.1 Kimlik modeli

`Target` (contract) yeni hali:

```python
class Target(BaseModel):
    kind: Literal["name", "handle"] = "name"   # geriye uyumlu varsayılan
    first_name: str = ""                        # name modunda zorunlu
    last_name: str = ""                         # her modda opsiyonel
    nickname: str = ""                          # handle modunda zorunlu
    name_forms: list[str] = []
    hints: dict[str, Any] = {}
    review_required: bool = False
```

`str = ""` seçildi, `str | None` değil: 37 yerde `f"{target.first_name} {target.last_name}"`
biçimlendirmesi var; `None` bunları `"None None"` yapar. Boş string + `.strip()` mevcut
kodun çoğunda zaten doğru sonucu veriyor.

**Doğrulama kuralları (API katmanı):**

| Mod | Zorunlu | Opsiyonel | Reddedilen |
|---|---|---|---|
| `name` | `first_name` (≥2 karakter) | `last_name`, `nickname` | ikisi de boşsa 422 |
| `handle` | `nickname` (normalize sonrası ≥3 karakter ve ≥1 alfanümerik; baştaki `@` temizlenir) | `first_name`, `last_name` | nickname boş / çok kısa / tamamen noktalama ise 422 |

- Nick normalize edilmiş haliyle değil, **ham haliyle** saklanır (görüntüleme için);
  eşleştirmede `normalize_turkish()` kullanılır.
- Nick tabanı, varyant üreticisinin tabanıyla **hizalı**: `variants.py:60` `<3` karakteri
  atıyor ve baş/son `._-` kırpıyor. Bu yüzden doğrulama da normalize sonrası ≥3 karakter
  ve ≥1 alfanümerik ister; `__`, `..`, `ab` gibi değerler sessiz boş tarama yerine 422 alır.
- `hints.username` **deprecated**: gönderilirse ve `nickname` boşsa `nickname`'e taşınır.
  `HINT_KEYS` içinde kalır (kırılmasın) ama dokümante edilir. Varyant üreticisi artık
  `hints`'ten nick **okumaz**; tek kaynak `Target.nickname`.

### 6.2 DB ve migration

`0007_target_identity`:

```sql
ALTER TABLE targets ADD COLUMN nickname VARCHAR(128);
ALTER TABLE targets ADD COLUMN kind VARCHAR(16) NOT NULL DEFAULT 'name';
ALTER TABLE targets ALTER COLUMN first_name DROP NOT NULL;
ALTER TABLE targets ALTER COLUMN last_name  DROP NOT NULL;
-- backfill: hint'lerde nick varsa birinci sınıf alana taşı
UPDATE targets SET nickname = COALESCE(hints->>'nickname', hints->>'username')
 WHERE nickname IS NULL AND (hints ? 'nickname' OR hints ? 'username');
```

`downgrade`: kolonları düşür, `first_name`/`last_name` NOT NULL'a döner (boş satır varsa
`''` ile doldurulur — downgrade veri kaybı notu docstring'e yazılır).

DB `nullable=True`, contract `""`: sınırda tek yerde normalize edilir
(`_load_contract_target`, `scanner.py` target kurulumu). Böylece DB dürüst, kod tarafı
`""` semantiğiyle çalışır.

### 6.3 API sözleşmesi

- `POST /cases`: `TargetInput` alanları + `kind`; `model_validator(mode="after")` ile
  6.1 matrisi uygulanır. `guard_target()` aynen çalışır (yaş sinyali `hints` +
  isim/nick birleşimi üzerinden).
- `GET /cases/{id}` ve `GET /cases/{id}/export`: `target` bloğuna `nickname` ve `kind`
  eklenir. Mevcut anahtarlar korunur (eklemeli değişiklik).
- `CandidateTarget`/`SeedInput` değişmez.

### 6.4 Engine

**Varyantlar** (`variants.py`): imza `generate_variants(first_name, last_name, hints,
nickname="")` olur (geriye uyumlu kwarg).
- `kind=handle`: nick varyantları (nick, nick+year, nick_dev, nick.dev) üretilir;
  isim blokları **yalnızca** isim verilmişse eklenir.
- `kind=name`: bugünkü davranış birebir korunur + elle verilen nick takviye olarak eklenir
  (bugün `hints` üzerinden olan yol, artık birinci sınıf alandan).

**Şablon planlayıcı** (`planner.build_template_fallback_queries`):
- `kind=handle` veya `fullname` boş: isim sorguları **hiç üretilmez**. Yerine
  `f'"{nick}" site:github.com'` biçiminde handle sorguları ve platform başına
  `site:` araması.
- `kind=name`: mevcut sorgu seti aynen kalır; nick varsa handle sorguları **eklenir**.
- Değişmez: üretilen hiçbir sorgu boş/`""` içermez (test edilir).

**LLM planlayıcı**: `render_prompt("query_planner", ..., kind=..., nickname=...)`.
`query_planner.jinja`'ya kural: "kind=handle ise isim/isim-soyad sorgusu üretme; yalnız
handle varyantları ve platform aramaları üret." Bütçe (`max_llm_calls`) değişmez.

**Tahmin** (`scan_modes.estimate_scan`): `generate_variants(...)` çağrısına `nickname`
geçirilir; `hint_count` aynı. Handle modunda varyant sayısı nick tavanına göre hesaplanır.

**Explainer**: `target_info` artık `display_name` + `(@nick)` biçiminde; isim yoksa
yalnız `@nick`. Redaksiyon aynı (`redact_text`).

#### 6.4.1 Nick engine'e nasıl ulaşır (karar)

`hints`'e **aynalama yok** (aksi §4 anti-hedefini çiğner). `generate_variants` imzasına
`nickname=""` kwarg'ı eklenir; `Target`'a erişimi olan tüm çağrı yerleri
`nickname=target.nickname` geçirir:

| Çağrı yeri | Bugün | Sonra |
|---|---|---|
| `apps/api/showis/services/scanner.py:324` | `generate_variants(f, l, hints)` | `+ nickname=target.nickname` |
| `packages/engine/showis_engine/scan_modes.py:400` | aynı | aynı |
| `packages/connectors/.../sitecheck/connector.py:110` | aynı | `+ nickname=ctx.target.nickname` |
| `packages/connectors/.../rendered.py:111` | aynı | aynı |
| `packages/connectors/.../publicapi/base.py:160` | aynı | aynı |
| `packages/connectors/.../hackthebox.py:129` | aynı | aynı |

Connector'lar `ctx.target`'a zaten sahip (`ScanContext`), yani yeni bağımlılık yok.
Bu yapılmazsa handle modunda varyant listesi **boş** olur ve sitecheck/publicapi hiç görev
üretmez — tarama sessizce boş döner.

#### 6.4.2 Connector'ların kendi isim sorguları (karar)

Handle modunda isimden üretilen sorgular **handle'a çevrilir**, susturulmaz:

| Dosya | Bugünkü sorun | Düzeltme |
|---|---|---|
| `github.py:90-111` | koşulsuz `"" in:name` (bozuk istek, kota israfı) | handle modunda `f"{nickname} in:login"`; isim yoksa isim sorgusu hiç üretilmez |
| `rendered.py:130-138` | `"" site:tryhackme.com` | handle modunda `"{nick}" site:tryhackme.com` |
| `websearch/connector.py:64-80` | fullname boşsa `[]` → **web araması tamamen kaybolur** | handle modunda nick sorguları üretir |
| `sitecheck/search_connector.py:66-68` | aynı şekilde arama atlanır | aynı |

Değişmez: hiçbir connector boş/`""` içeren sorgu göndermez (test edilir).

#### 6.4.3 Planner çıktısı gerçekten çalıştırılır — Faz 2 (KARAR: yapılacak)

Bugün `QueryPlan` üretiliyor ama **hiç tüketilmiyor**: `scanner.py:331-338` yalnızca
`len(query_plan.queries)`'i logluyor; `grep query_plan` → 3 satır, hepsi log.

Faz 2'de plan gerçek görevlere dönüşür. Tasarım:

**Dönüşüm** (`scanner.py`, connector `plan()` turundan hemen sonra):

```python
for q in query_plan.queries[:planner_cap(scan.mode)]:
    spec = TaskSpec(
        id=str(uuid.uuid4()),
        connector="websearch",
        platform=q.platform,
        kind="search",
        params={"query": finalize_query(q.query), "limit": self._planner_limit,
                "source": "planner"},
        priority=min(max(int(q.priority), 1), 5),
    )
```

- `finalize_query` (`planner.py:601-613`) **hem** LLM hem şablon yoluna uygulanır
  (bugün yalnız LLM çıktısına uygulanıyor) → sanitize + yasak desen kapısı.
- `source="planner"` işareti audit/log ayrımı için; connector'lar bunu yok sayar.

**Tavanlar ve mod kapısı:**

| Mod | planner sorgu tavanı | Gerekçe |
|---|---|---|
| O1 | **0** | hızlı mod: 420 sn, 140 görev; 30 arama sığmaz |
| O2 | 8 | dengeli |
| O3 | 20 | derin tarama |

- Tavan, `scan_modes.py` içine `planner_cap(mode)` fonksiyonu olarak eklenir (mod
  profilleriyle aynı yerde, sihirli sayı dağınıklığı olmasın).
- Planner görevleri **normal `TaskSpec`** olduğu için mevcut bütçe kapısına zaten tabidir:
  `_execute_tasks` her dispatch öncesi `max_tasks` / `max_http_requests` kontrol ediyor
  (`scanner.py:676-681`). Ek muhasebe gerekmez, çift sayım da olmaz.
- `max_llm_calls` (LLM bütçesi) ile karıştırılmaz; bu tavan **arama çağrısı** sayısıdır.

**Tekilleştirme:**
- Planner sorguları kendi içinde normalize edilip tekilleştirilir;
  `finalize_query` sonrası boşalan veya `""` (boş tırnak) içeren sorgu **atılır**.
- Connector'ların ürettiği arama sorgularıyla normalize metin eşleşmesi varsa planner
  kopyası atılır (aynı aramayı iki kez ödememek için).
- Sayı log'a gerçek eklenen görev sayısı olarak yazılır (bugünkü "N sorgu üretti" yanıltıcı
  mesajı düzeltilir).

**Kabul:** O2'de bir taramada `source=planner` görevleri DB'de görünür, sayı ≤ 8,
`""` içeren sorgu yok, O1'de hiç yok.

**Risk azaltma:** Faz 2, Faz 1 tamamlanıp `make gate` yeşil olduktan **sonra** başlar.
Ayrı commit'ler; gerekirse `planner_cap` tek satırla 0'a çekilerek kapatılabilir.

### 6.5 Skorlama

- `ConfidenceBreakdown`'a `handle_exact: float = 0.0` (0.15) eklenir: profil handle'ı
  hedef nick ile normalize edilmiş halde birebir eşleşiyorsa verilir. Bu, Sherlock'un
  "bulundu" sinyalinin dürüst karşılığı.
- **`handle_exact` ile `username_exact_match` birbirini dışlar** (çift sayım yok):
  - aynı kullanıcı adı **≥2 platformda** → `username_exact_match = 0.30`, `handle_exact = 0`
  - yalnız **1 platformda** birebir → `handle_exact = 0.15`, `username_exact_match = 0`
  Gerekçe: `resolver.py:77-86` zaten ≥2 platform için 0.30 veriyor; aynı kanıtı ikinci kez
  saymak bio ipucuyla (0.05) birlikte 0.50 CANDIDATE eşiğini haksız yere açar.
- **Yeniden normalizasyon yok.** `name_similarity` handle modunda yapısal olarak 0 kalır;
  tavan 1.10 → clamp 1.0. Eşikler (0.80/0.50) değişmez.
- LEAD koşulu genişletilir: `name_similarity > 0 or bio_location_company > 0` →
  `... or handle_exact > 0`. Gerekçe: handle modunda tek platformda birebir eşleşme
  bugün REJECTED'a düşüyor; bu bir hata sınıfı, düzeltiliyor.

### 6.6 Arayüz

`/cases/new` üstünde segment: **İsimle ara** | **Kullanıcı adıyla ara**.

```
[ İsimle ara ] [ Kullanıcı adıyla ara ]
┌──────────────────────────────────────┐
│ Kullanıcı adı *  @ [ abdulkeremdemir ]│
│ Ad      (ops.)  [                   ] │
│ Soyad   (ops.)  [                   ] │
│ Okul / Şehir / Şirket (ops.)          │
└──────────────────────────────────────┘
```

- İsim modu: Ad* önde/otofokus, Soyad ve Nick opsiyonel.
- Handle modu: Nick* otofokus, `@` öneki görsel, Ad/Soyad "doğrulama için" etiketiyle.
- Nick yazılırken `hints.nickname` değil `target.nickname` gönderilir.
- Vaka detay chip'i: `@nick` / `Ad Soyad` / `Ad Soyad · @nick` (ikisi de varsa).
- Vaka listesi ve tarama sayfası `display_name` kullanır.

### 6.7 Rapor ve dışa aktarma

- `report_service` target bloğu: `nickname`, `kind` eklenir.
- `engine/report.py` (2 şablon) `display_name` kullanır; isim yokken **boş satır
  basılmaz**, yerine `@nick` yazılır.
- `contracts`'a yardımcı: `Target.display_name` (ad-soyad, yoksa `@nick`, yoksa `""`).

### 6.8 Geriye uyumluluk

- `kind` varsayılanı `"name"` → mevcut istemciler aynı davranır.
- `first_name`/`last_name` contract'ta `str = ""` → mevcut çağrılar kırılmaz.
- Eski `hints.username` kabul edilir, taşınır.
- Migration eklemeli; mevcut 9 saatlik canlı veride yeniden yazım yok.

## 7. Riskler

| Risk | Etki | Azaltma |
|---|---|---|
| 37 `first_name` referansı, boş isimde bozuk sorgu/etiket üretir | Yanlış sorgu → boşa kota | `" "` içeren sorgu testi; `display_name` yardımcısı; gözden geçirme listesi |
| Skorlama davranışı isim modunda kayar | Geçmiş bulgu skorları değişir | Mevcut resolver testleri **aynen** geçmeli; yeni alan varsayılan 0 |
| Migration canlı DB'de ters gider | Vaka açılamaz | `pg_dump` + `alembic upgrade`/`downgrade -1` prova; tek transaction |
| Handle modu LLM prompt'u eski davranışı sürdürür | İsim sorguları üretilir | `kind` prompt'a açıkça geçer + şablon fallback testi |
| `handle_exact` yanlış pozitif (platform adı taşıyan jenerik hesap) | Şişmiş skor | Yalnız probe edilen platformda, normalize birebir eşleşme; 0.15 sabit; ≥2 platformda devre dışı |
| Connector'larda boş isim → `"" in:name` | Bozuk istek, kota israfı, 429 | 6.4.2'deki handle dönüşümü + "boş sorgu yok" testi |
| Handle modunda web araması sessizce kaybolur | Kapsama kaybı, kullanıcı "sonuç yok" sanır | `websearch`/`sitecheck` handle sorgusu + kabul testi |
| `make gate` yeniden üretilmiş şema bekler (`types.generated.ts`) | Kapı FAIL | `make schemas` sonrası üretilen dosya commit'e girer |
| Faz 2'de planner görevleri arama kotasını şişirir | Para, 429, uzun tarama | Mod başına tavan (O1=0/O2=8/O3=20) + mevcut bütçe kapısı + tekilleştirme |
| Faz 2 isim modunda davranışı değiştirir | Beklenmedik bulgu/süre artışı | Ayrı commit, `planner_cap` 0'a çekilerek kapatılabilir, O1'de kapalı |

## 8. Test planı

1. **API matrisi** (yeni): name / name+surname / name+nickname / handle /
   handle+name / ikisi de boş / `ab` / `__` / `..` / `@nick` / `hints.username` taşıma →
   201/422.
2. **Regresyon**: mevcut `tests/integration/test_api_cases.py` gövdeleri aynen 201 dönmeli.
3. **Migration**: `alembic upgrade head` + `downgrade -1` boş ve dolu DB'de.
4. **Variants**: handle modunda isim varyantı yok; name modunda mevcut çıktı birebir aynı.
5. **Planner**: handle modunda `""` içeren sorgu yok, `site:github.com "nick"` var;
   isim modunda üretilen sorgu seti birebir aynı.
6. **Scoring** (üç ayrı vaka):
   - tek platform birebir handle → `handle_exact=0.15`, `username_exact_match=0`, LEAD
   - iki platform → `username_exact_match=0.30`, `handle_exact=0` (çift sayım yok)
   - isim modu → mevcut resolver testleri **birebir** geçer (skor değişmez)
7. **Rapor/export**: `nickname` ve `kind` var; isim boşken `None`/boş satır yok.
8. **Connector'lar** (yeni): handle modunda `github` → `in:login`, `rendered`/`sitecheck`/
   `websearch` nick sorgusu üretir; hiçbirinde `""` yok.
9. **UI**: iki modda vaka açma akışı elle (browse) doğrulanır; `npx tsc --noEmit` ve
   `npm run build` geçer. **Yeni Playwright altyapısı bu kapsamda kurulmaz**
   (`apps/web/package.json`'da test script'i ve Playwright bağımlılığı yok).
10. **Gate**: `make gate` (= `bash scripts/final_gate.sh`) — ruff, mypy, pytest,
    `tsc --noEmit`, `next build`, `make schemas` + `apps/web/lib/types.generated.ts` diff,
    secret taraması. (`scripts/gate_checks.py` bu repoda yok; gerçek kapı
    `final_gate.sh`.) Uçtan uca: `bash scripts/final_gate.sh --live`
    (`scripts/e2e_acceptance.sh --strict`). `make schemas` ayrıca
    `packages/contracts/schemas/*.json` üretir — ikisi de commit'e girer.

### 8.1 Faz 2 testleri (planner aktivasyonu)

11. **Dönüşüm**: `QueryPlan` → `TaskSpec(connector="websearch", params.source="planner")`;
    `finalize_query` uygulanmış, yasak desenli sorgu görev üretmiyor.
12. **Tavan**: O1 → 0 görev, O2 → ≤8, O3 → ≤20 (DB'deki `tasks` satırlarıyla ölçülür).
13. **Tekilleştirme**: aynı normalize sorgu iki kez görev olmuyor; connector'ın ürettiği
    sorguyla birebir aynıysa planner kopyası atılıyor.
14. **Bütçe**: `max_tasks` düşük verildiğinde planner görevleri bütçeyi aşmıyor
    (mevcut `_budget_reason` kapısı devrede).
15. **Regresyon**: O1 taramasında `source=planner` görevi yok; isim modu skorları değişmedi.

## 9. Kabul kriterleri

- Sadece nick girilerek vaka açılıp tarama başlatılabiliyor.
- Soyad hiçbir formda zorunlu değil; API'de soyadsız isim modu 201 dönüyor.
- Handle modu taraması isim sorgusu üretmiyor; connector'lar nick ile görev üretiyor
  (kanıt: üretilen `TaskSpec` listesi, `""` içeren sorgu yok).
- Handle modunda web/arama connector'ları boş dönmüyor (kapsama kaybı yok).
- İsim modunda mevcut davranış ve testler birebir korunuyor (2983 testin ilgili alt kümesi).
- Rapor, isim yokken `@nick` gösteriyor.
- (Faz 2) O2 taramasında planner kaynaklı arama görevleri DB'de görünüyor, tavan
  aşılmıyor, tekrar eden sorgu yok; O1'de planner görevi yok.

## 10. Builder'a bırakılanlar

- `display_name` yardımcısının tam yerleşimi (contracts property vs engine util).
- Migration dosya adı ve downgrade stratejisi detayı.
- UI segment bileşeninin mevcut tasarım sistemiyle uyumu.
- Test dosyası isimleri/organizasyonu.
- `/cases` liste endpoint'ine `nickname`/`kind` eklenmesi; `estimate` endpoint'inin
  `_load_contract_target` üzerinden gitmesi; `executive_summary.target_name` alanının
  `display_name`'e bağlanması.
- `guard_target`'ın yaş sinyali taramasına nick'in de eklenmesi.
