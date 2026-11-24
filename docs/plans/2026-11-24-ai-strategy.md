# Showis — "En zeki model" stratejisi, rakip boşluk analizi ve uygulama planı

## Context
Showis bugün çalışan ama **zayıf bağlanmış** bir AI katmanına sahip:
- `packages/llm/showis_llm/client.py` tek bir OpenAI-uyumlu `chat/completions` POST'u yapıyor: **native tool-calling yok, streaming yok, 8 sn timeout** (reasoning modelleri için öldürücü), varsayılan modeller `gpt-4o-mini` / `minimax-m3.1-flash` sınıfında.
- Araştırma ajanı (`apps/api/showis/services/research.py`) JSON-şemalı "sahte tool" adımlarıyla çalışıyor; kullanıcıyla etkileşim **tur bazlı** (`POST .../messages` çalışırken 409), UI düz metin.
- Havuz (`PoolItem`) öğeler arasında **ilişki tutmuyor**; "muhakeme" = `format_context` ile metin dökümü. Hipotez, çelişki, kanıt zinciri yok.
- İhlal kontrolü yalnız **ücretli HIBP** anahtarıyla çalışıyor (`services/hibp.py`), anahtar yoksa 503.

Hedef: (1) en güçlü modellere native erişim, (2) gerçek interaktif AI modu, (3) havuz üzerinde kanıt temelli derin muhakeme, (4) anahtarsız/ücretsiz sızıntı kontrolü.

---

## Rakiplere karşı agresif boşluk listesi

| # | Rakip / referans | Onlarda olan | Showis'te eksik | Öncelik |
|---|---|---|---|---|
| 1 | Maltego, ShadowDragon Horizon | Varlıklar arası tipli ilişki grafı, "transform" pivotları | Havuzda ilişki yok; pivot tek tık değil | P0 |
| 2 | OSINT Industries, Epieos | E-posta/telefon → yüzlerce modülde tersine arama, tek ekranda sonuç | E-posta/telefon pivot zinciri sığ; telefon için hiçbir şey yok | P1 |
| 3 | Social Links (AI), 2026 "agentic OSINT" araçları | Ajan araç seçer, pivotlar, rapor taslağı yazar, analistle canlı konuşur | Tur bazlı, araya girilemiyor, ajan soru soramıyor | P0 |
| 4 | Perplexity / ChatGPT Deep Research tarzı ajanlar | Token streaming, düşünme özeti, canlı kaynak kartları, markdown | Düz metin, token streaming yok | P0 |
| 5 | SpiderFoot | 200+ modül + **korelasyon kuralları** (YAML) | Deterministik korelasyon motoru yok | P1 |
| 6 | HIBP, XposedOrNot, Hudson Rock | İhlal + infostealer görünürlüğü | Yalnız ücretli HIBP; infostealer hiç yok | P0 |
| 7 | Maltego/i2 | Zaman çizelgesi, coğrafi tutarlılık | Timeline / konum çelişki analizi yok | P2 |
| 8 | Tüm kurumsal araçlar | Kanıt arşivi (sayfa snapshot + hash) | Roadmap'te açık: kanıt yönetimi | P1 |
| 9 | Model tarafı | Reasoning modelleri, uzun bağlam, prompt caching | 8 sn timeout, mini modeller, cache yok, model rolleri ayrılmamış | P0 |
| 10 | Analist araçları | "Neden bu sonuca vardın?" açıklanabilirlik | Güven skoru var, gerekçe zinciri yok | P1 |

---

## Faz 1 — Model katmanı: en güçlü modellere native erişim (P0)

**Dosyalar:** `packages/llm/showis_llm/client.py`, `presets.py`, `base_url.py`, yeni `packages/llm/showis_llm/adapters/{openai_compat,anthropic}.py`, `apps/api/showis/services/ai_providers.py`, `connectors_factory.build_llm_client`.

1. `LLMClient`'ı adapter mimarisine böl: `complete()` korunur (geriye uyum), yanına
   - `stream(messages, tools=…) -> AsyncIterator[LLMEvent]` (text_delta, tool_call, thinking_summary, usage),
   - `complete_with_tools(messages, tools)` — **native** tool-calling (OpenAI `tools` / Anthropic `tool_use`).
2. **Native Anthropic adapter** (`/v1/messages`): extended thinking, prompt caching (sistem prompt + havuz bağlamı cache'lenir), uzun bağlam. Preset: `anthropic` → varsayılan `claude-opus-5-5`, ekonomik rol `claude-sonnet-5-5`, ucuz çıkarım `claude-haiku-5-5`. OpenRouter/OpenAI/Groq presetleri korunur; OpenAI-uyumlu yol tool+stream desteği kazanır.
3. **Model rolleri (routing):** profil başına `roles = {planner, reasoner, extractor, summarizer}`. Planlama/muhakeme en güçlü modelde, sayfa çıkarımı/özet ucuz modelde. `AIProvider` tablosuna `roles` JSON + migration `0018_ai_provider_roles` (`down_revision="0017_legacy_llm_profile"`).
4. Timeout/retry: role göre (reasoner 180 sn, extractor 30 sn), stream'de idle-timeout. `breaker.py` aynen kullanılır.
5. Yetenek keşfi: `discover_models` sonucunu `supports_tools/stream/thinking` bayraklarıyla sakla; desteklemeyen model için mevcut JSON-şema yoluna otomatik düş.
6. Değişmezler: Fernet şifreleme, `validate_base_url` allowlist ve `redact.py` her adapter'da zorunlu çağrılır.

## Faz 2 — Gerçekten interaktif AI modu (P0)

**Dosyalar:** `research.py`, `apps/api/showis/api/v1/research*.py`, `packages/contracts`, `apps/web/app/cases/[id]/research/page.tsx`, `apps/web/lib/api.ts`.

1. **Ajan döngüsünü native tool loop'a taşı:** `TOOL_DEFINITIONS` aynen tool şemasına çevrilir; `_run_subagent`/`_supervisor_plan` yeni `complete_with_tools`'u kullanır. Ölü kod (`_execute_tool`, `_build_llm_messages`) silinir.
2. **Çalışırken araya girme:** 409 yerine mesaj kuyruğu — `POST .../messages` aktif oturumda `steer` olarak kuyruğa alınır, ajan bir sonraki adım sınırında okur. `POST .../interrupt` (durdur), `POST .../resume`.
3. **Ajan kullanıcıya soru sorabilir:** yeni tool `ask_user(question, options[])` → oturum `awaiting_user` durumuna geçer, UI seçenekli kart gösterir, cevap gelince devam.
4. **Satır içi onay kartları:** `add_pool_item` sonuçları chat'te "Onayla / Reddet / Düzenle" kartı olarak gelir (PATCH pool). `confirmed` yalnız kullanıcı tıklamasıyla — invariant korunur.
5. **SSE genişletme:** mevcut `research_event_stream` üzerine `token`, `tool_start/tool_end`, `thinking_summary`, `question`, `proposal` olayları. WebSocket gerekmez.
6. **UI:** markdown render (react-markdown + sanitize), canlı araç zaman çizelgesi, kaynak kartları, "Hipotez panosu" yan paneli (Faz 3), hızlı komutlar (`/pivot email`, `/derinleştir`, `/çelişkileri göster`).
7. Sözleşmeler `packages/contracts`'a eklenir → `make schemas` ile `types.generated.ts`.

## Faz 3 — Havuzda derin muhakeme (P0/P1)

**Dosyalar:** `apps/api/showis/db/models.py`, `services/pool.py`, yeni `services/reasoning/{graph.py,pivots.py,correlate.py,hypotheses.py}`, `services/identity_graph.py` (yeniden kullanılır), migration `0019_pool_relations_hypotheses`.

1. **Tipli ilişki grafı:** `PoolRelation(case_id, src_item, dst_item, type, confidence, evidence[], status)` — tipler: `same_person, owns_account, uses_email, linked_from, located_in, works_at, alias_of, contradicts`. AI yazdığı ilişki de `proposed/candidate`.
2. **Pivot motoru (deterministik, Maltego-transform muadili):** her `kind` için pivot listesi — email → gravatar/breach/infostealer/username türetme; username → sitecheck+publicapi; domain → recon/whois; name+ipuçları → kullanıcı adı permütasyonları; url → linkfollow + `parse_page_intel`. Var olan connector'lar (`connectors_factory.build_connectors`) ve `research_playbook` fonksiyonları kullanılır. Ajan bunu `run_pivot(item_id, pivot)` tool'u ile çağırır; UI'da sağ tık "Pivot".
3. **Korelasyon kuralları (SpiderFoot muadili):** YAML kurallar (ör. "aynı avatar dHash + aynı bio linki → same_person +0.3"; "2 bağımsız kaynak yoksa candidate'e yükselme"). `identity_graph.compute_identity_clusters` union-find'ı pool ilişkileriyle beslenir.
4. **Hipotez nesnesi:** `Hypothesis(statement, confidence, supporting[], contradicting[], next_checks[])`. Reasoner model havuz grafını (alt-graf + kanıt) alır, hipotez üretir/günceller; skor kanıtlardan log-odds ile hesaplanır, LLM yalnız gerekçe yazar → halüsinasyon kontrolü.
5. **Çelişki & tutarlılık:** zaman çizelgesi (tarihli öğeler), konum/yaş/meslek uyumsuzluğu → `contradicts` ilişkisi + uyarı.
6. **Bağlam paketleme:** `format_context` yerine grafın önem-sıralı özeti + onaylı öğeler önce; Anthropic'te prompt cache ile ucuz tekrar.
7. **Kanıt arşivi:** her alıntılanan sayfa için metin snapshot + sha256 (roadmap'teki açık madde).

## Faz 4 — Ücretsiz sızıntı/ihlal kontrolü (P0)

**Dosyalar:** `services/hibp.py` → `services/breach/{__init__,xposedornot,hudsonrock,pwned_passwords,hibp}.py`, `api/v1/pool.py` breach-check ucu, `research.py::_run_breach_checks`, settings UI.

1. **Sağlayıcı zinciri (anahtarsız varsayılan):**
   - **XposedOrNot** `GET api.xposedornot.com/v1/check-email/{email}` (+ breach analytics) — ücretsiz, anahtarsız; ~2 istek/sn, günlük IP limiti → cache + rate limiter (engine HTTP bütçesi).
   - **Hudson Rock Cavalier** ücretsiz OSINT uçları (email/username/domain → infostealer bulguları; maskeli veri).
   - **Pwned Passwords range API** (k-anonimlik, SHA-1 ilk 5 karakter) — kullanıcının *kendi* parolası/hash'i için "bu parola sızmış mı" kontrolü; parola sunucuya tam gönderilmez, saklanmaz.
   - **HIBP v3** anahtar girilmişse ek kaynak olarak kalır.
2. Sonuçlar birleşik `BreachReport` sözleşmesine normalize edilir: breach adı, tarih, veri sınıfları, "parola sızmış: evet/hayır", infostealer var/yok, kaynak sağlayıcı. `attributes.breach_check` formatı genişletilir (geriye uyumlu).
3. Ucun davranışı: anahtar yoksa artık 503 değil; ücretsiz zincir çalışır. Tüm sağlayıcılar başarısızsa 502.
4. **Sınır (bilinçli tercih):** üçüncü kişilere ait **düz metin parolalar getirilmez/saklanmaz/gösterilmez** (COMB/ProxyNova tarzı kaynaklar entegre edilmez). Bu hem yasal risk hem invariant ("yalnız herkese açık, erişim kontrolü ihlali yok") gereği. Gösterilen: sızıntı var mı, hangi olayda, hangi veri tipleri.
5. Testler `respx` ile mock'lanır; gerçek ağa çıkılmaz.

## Faz 5 — Kapsam genişletme (P1/P2)
- Telefon pivotları (E.164 normalize, ülke/operatör çıkarımı, herkese açık profil aramaları).
- Tersine görsel: mevcut avatar dHash'i havuz geneline yay; bulunan görsellerde dHash eşleştirme.
- Kayıtlı arama + izleme (yeni sızıntı/hesap çıktığında bildirim) — roadmap "monitoring" maddesi.
- Rapor: hipotez + kanıt zinciri + çelişkiler bölümü (PDF/HTML).

## Bilinçli olarak yapılmayacaklar
- Giriş duvarı / CAPTCHA aşma, şifre sıfırlama formu üzerinden hesap varlığı yoklama (holehe tarzı) — ürün invariant'ı ile çelişir.
- AI'ın `confirmed` yazması.

## Doğrulama
- Her faz sonunda `make gate` (ruff, mypy, pytest, tsc, next build, şema drift, secret scan).
- Yeni testler: `tests/test_llm_adapters.py` (respx ile tool_call + SSE stream parse), `tests/test_research_interactive.py` (steer kuyruğu, `ask_user` → `awaiting_user` → devam, AI yazımı asla `confirmed` değil), `tests/test_pool_relations.py` + `test_correlation_rules.py`, `tests/test_breach_providers.py` (XON/Hudson Rock/k-anonimlik; tam parola/hash'in ağa gitmediğini doğrula).
- Migration zinciri: `alembic upgrade head` SQLite testte; `0018`, `0019` `down_revision` doğru.
- Canlı: `bash scripts/e2e_acceptance.sh --strict` + UI'da bir vakada: seed → ajan soru soruyor → kullanıcı cevaplıyor → çalışırken yönlendirme → öneri kartı onayı → hipotez panosu → breach raporu (anahtarsız).

## Faz 6 — Kalan işler (Faz 1–4 push'landı: 9f369e5, 503b31b, bfd57b8, 3680f1b)
Sonnet 5.5 (medium effort) arka plan ajanına verilecek; sıra:
1. **Ajan yazma araçları:** `add_pool_item`, `add_supply_chain_node/edge`, `start_scan` ve yeni `run_pivot(item_id, pivot)` native tool döngüsüne bağlanır. Alt ajanlar DB'siz paralel çalıştığı için yazmalar kuyruğa alınıp ana döngüde tek DB oturumunda uygulanır. Yazımlar yalnız `proposed`/`candidate`; `services/reasoning/pivots.py` yeniden kullanılır.
2. **Kalıcı kontrol durumu:** steer kuyruğu, `ask_user` sorusu/cevabı ve interrupt bayrağı DB'ye taşınır (migration `0021_research_control_state`, `down_revision="0020_pool_relations_hypotheses"`). API yeniden başlayınca `awaiting_user` oturumu soruyu korur, cevapla devam eder.
3. **Nihai rapor/biyografi akışı:** `_synthesize_report`/`_synthesize_bio` `stream()` ile `token` olayı yayar; şema doğrulaması sonunda yapılır, akış yoksa eski yol.
4. **Kanıt arşivi:** `EvidenceSnapshot(case_id, url, sha256, text, fetched_at)`; `fetch_page` ve alıntılanan her sayfa kaydedilir, atıflar snapshot'a bağlanır, UI'da "Kanıt" görünümü.
5. **Korelasyon kuralları YAML:** `RULES` → `services/reasoning/rules.yaml` + yükleyici/doğrulayıcı; Python kuralları varsayılan olarak korunur.
6. **Faz 5:** telefon pivotu (E.164 normalize, ülke çıkarımı, herkese açık aramalar), avatar dHash'in havuz geneline yayılması, kayıtlı izleme (yeni sızıntı/hesap → bildirim; Celery beat varsa onunla), PDF/HTML raporda hipotez + kanıt zinciri + çelişki bölümü.
7. **Ortama bağlı test:** `test_rapor_silme_basarisizsa_audit_yazilir` root'ta `chmod` ile hata üretemiyor; testi atlamadan silme çağrısını monkeypatch ile hata fırlatacak şekilde düzelt, böylece `make gate` yeşile döner.

**Durum:** 1–7 tamam; `make gate` yeşil (canlı E2E hariç).
- 4 Kanıt arşivi: `0022_evidence_snapshots`, `evidence_archive`, `/evidence/snapshots` uçları, atıf→snapshot bağı, saklama süresi temizliği, web "Kanıt" görünümü, `test_evidence_archive.py`.
- 5 Korelasyon YAML: `services/reasoning/rules.yaml` + `load_rules/parse_rules` (geçersiz dosya → Python varsayılanları), `shared_attribute` bildirimsel kural türü, `test_correlation_rules.py`. `pyyaml` bağımlılık + `infra/requirements.lock`.
- 6 Faz 5: telefon (`services/phone.py`, `phone_normalize`/`phone_search` pivotları, `same_phone` kuralı); avatar dHash yayılımı (`reasoning/avatars.py`, `avatar_hash` pivotu, `POST .../reasoning/avatars/hash`, `same_avatar` Hamming ≤ 6 + düz renk/kalabalık dışlama); kayıtlı izleme (`0023_watch_alerts`, `services/watch.py`, `/watch/*` uçları, Celery beat `showis.watch.run` 6 saatte bir, vaka webhook'u, web "İzleme"); rapora "Hipotezler, Kanıt Zinciri ve Çelişkiler" bölümü (HTML/PDF/JSON; `proposed` ilişkiler kanıt zinciri sayılmaz).
- Kalan (kullanıcı makinesinde): `make docker-rebuild` + migration `0022`, `0023` uygulaması, `bash scripts/final_gate.sh --live`, UI'da canlı doğrulama. Ajan Docker/gerçek API çalıştıramaz.

Kurallar: AGENTS.md, ağ mock'lu testler, `make schemas`, her yeşil dilimde commit + push, PR yok. Canlı doğrulama (Docker, gerçek API'ler) kullanıcının makinesinde yapılacak; ajan yapamaz.

## Uygulama sırası
Faz 1 → Faz 4 (bağımsız, hızlı kazanım) → Faz 2 → Faz 3 → Faz 5. Her faz ayrı commit seti, `claude/showis-ai-model-strategy-8ozojg` dalına.
