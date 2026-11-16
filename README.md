# Showis — OSINT İnceleme Aracı

Ad + soyad (ve ipuçları) ile başlayarak kamuya açık kaynaklarda platform
hesaplarını bulan, cross-platform kanıt zinciri kuran ve güven skoru ile
raporlayan kişisel OSINT web uygulaması.

> **Yasal çerçeve:** Yalnızca herkese açık sayfalar/API'ler taranır. Giriş
> duvarı, CAPTCHA bypass, unofficial API ve yasak host'lar kodda yoktur.
> Bkz. `docs/legal/`.

## Özellikler

- **22+ Zengin Connector:** GitHub, GitLab, Reddit, Medium, LeetCode, Codeforces,
  Keybase, DockerHub, Dev.to, HackerNews, NPM, PyPI, Chess, Steam, Gravatar,
  Bluesky, HuggingFace, Kitsu, SoundCloud, WordPress, TryHackMe, HackTheBox.
- **52+ Platform Site-Check:** Kullanıcı adı varlık doğrulaması (Kaggle, HackerRank, Root-Me, Duolingo, Replit, vb.).
- **Görsel Avatar Eşleme (dHash):** Farklı platformlarda yeniden boyutlandırılmış/sıkıştırılmış aynı avatarları tespit eden algısal 64-bit fark karması (perceptual difference hash) ve birebir SHA-256.
- **LLM Dinamik Planlayıcı & Açıklayıcı:** OpenCode Go / OpenAI uyumlu BYOK entegrasyonu; otomatik veri redaksiyonu (`redact_text`) ve şablon fallback desteği.
- **Hibrit Arama Sağlayıcısı (Hybrid Search):** Brave, SerpAPI, Google CSE ve DuckDuckGo fallback zinciri; URL tekilleştirme.
- **Varyant Motoru:** Türkçe karakter normalizasyonu + akıllı kullanıcı adı adayları.
- **Deterministik Resolver:** 7 sinyalli güven skoru ve çelişki tespiti (eşikler: ≥0.80 confirmed, 0.50-0.79 candidate).
- **Canlı Tarama & Görselleştirme:** SSE ile gerçek zamanlı akış, Cytoscape.js grafiği, Türkçe/İngilizce raporlama.
- **Uyumluluk:** KVKK saklama süreleri, denetim izi (audit log), kaskad silme, JSON/PDF dışa aktarma.

## Kurulum

### Docker (önerilen)

```bash
cp .env.example .env
# MASTER_KEY üret
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" >> .env

make docker-up     # tüm servisler
docker compose -f infra/docker-compose.yml ps
```

Arayüz: `http://localhost:8083` — API: `http://localhost:8000/health`

### Geliştirme (docker'sız)

```bash
# Postgres + Redis gerekli — portları host'a açmak için:
docker compose -f infra/docker-compose.yml -f infra/docker-compose.dev.yml up -d postgres redis
make dev           # api :8000 + web :3000
make test          # pytest
make lint          # ruff
make typecheck     # mypy
make schemas       # JSON Schema export
make eval          # eval seti precision@3 raporu
```

## Kullanım

1. **Ayarlar** → sağlayıcı anahtarlarını gir (BYOK, maskeli saklanır)
2. **Yeni Vaka** → amaç + hukuki dayanak + hedef ad/soyad + ipuçları + seed'ler
3. **Tarama** başlat → canlı görev akışını izle
4. **Bulgular** → skor kırılımı + kanıt paneli → Onayla/Reddet
5. **Grafik** → entity/edge ağı
6. **Rapor** → PDF/JSON üret + indir

## Mimari

```
apps/api     FastAPI + SQLAlchemy(async) + Alembic — REST + SSE
apps/web     Next.js 15 + Tailwind — arayüz
packages/contracts   Pydantic modeller (tek doğruluk kaynağı)
packages/connectors  github, tryhackme, hackthebox, websearch
packages/engine      http, policy(SSRF/robots), variants, resolver,
                     planner, browser(client+service), report, links
packages/llm         OpenAI-uyumlu istemci + prompt'lar + redaksiyon
packages/settings    Fernet şifreli BYOK
infra/       docker-compose, Dockerfile'lar, caddy, script'ler
```

Detaylar: `docs/runbook.md`, `docs/legal/`, spesifikasyon `.opencode/plan/showis-osint-implementation-spec.md`.
