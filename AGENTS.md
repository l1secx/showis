# AGENTS.md — Showis

Kişisel OSINT/araştırma platformu. API FastAPI + async SQLAlchemy, web Next.js 15 App Router.
Ürün ve mimari özeti `README.md`; operasyon `docs/runbook.md`; şema akışı `docs/SCHEMAS.md`.

## Hızlı doğrulama

Sıra: `lint -> typecheck -> test -> web build`. Hepsi için:

```bash
make gate          # ruff + mypy + pytest + tsc + next build + şema drift + secret scan
```

Tek tek:

```bash
make lint
make typecheck
make test
make web-build     # kökteki package.json yalnız npm installer'ı; web dizini apps/web
```

Tek test (PYTHONPATH zorunlu; `make test` bunu kendisi kurar):

```bash
PYTHONPATH=apps/api:packages/contracts:packages/settings:packages/llm:packages/engine:packages/connectors \
  .venv/bin/pytest tests/test_planner.py::test_adi -q
```

Kurulum: `make bootstrap` (yerel `showis-*` paketleri PyPI'da yok, editable kurulur).

## Test ve ortam tuzakları

- NixOS'ta doğrudan `.venv/bin/pytest` `greenlet -> libstdc++.so.6` hatası verir. `make test`
  LD_LIBRARY_PATH'i kurar. Elle çalıştırırken:
  `export LD_LIBRARY_PATH=$(dirname "$(cc -print-file-name=libstdc++.so.6)")`.
- Testler varsayılan SQLite in-memory kullanır; Postgres/Redis gerekmez. Ağ çağrıları
  `respx`/fake connector ile mock'lanır; gerçek ağa çıkan test yazma.
- `infra/.env` dosyasını host pytest'ine `source` etme: `USE_CELERY=1` ve `postgres`/`redis`
  servis adları sızar, testler gerçek broker'ı arar. Gerekli değişkeni tek tek ver.
- `pyproject.toml` `faulthandler_timeout=120`: asılan test stack dökümüyle FAIL olur, sonsuz beklemez.
- Canlı kabul: `bash scripts/e2e_acceptance.sh --strict`; `SHOWIS_API_TOKEN`,
  `SHOWIS_BASIC_AUTH_USER`, `SHOWIS_BASIC_AUTH_PASSWORD` gerekir. `make gate --live` değil,
  `bash scripts/final_gate.sh --live`.

## Mimari sınırlar

- `apps/api/showis/api/v1/*` router'ları `/api/v1` altında; auth bağımlılıkları `main.py`'de.
  `/health` kimliksizdir; diğer uçlar `SHOWIS_API_TOKEN` yoksa fail-closed `503`.
- `apps/api/showis/services/` iş mantığı; `scanner.py` tarama, `research.py` derin ajan,
  `research_playbook.py` deterministik sorgu/link/biyografi çıkarımı, `pool.py` havuz.
- `packages/contracts` tek sözleşme kaynağıdır. `make schemas` yalnız
  `apps/web/lib/types.generated.ts` üretir; `apps/web/lib/types.ts` el yazımıdır, üretilen
  dosyayı `export *` ile dışa aktarır. İkisini elle karıştırma.
- `packages/connectors` connector'lar (websearch, sitecheck, publicapi, linkfollow);
  `packages/engine` HTTP bütçesi, SSRF/robots policy, variant/planner/scan modes.
- Web API çağrıları `apps/web/app/api/[...path]` proxy'sinden geçer; token tarayıcıya değil
  sunucu tarafına eklenir. Yeni uçta `apps/web/lib/api.ts` kullan.

## Docker / deploy

- Compose: `infra/docker-compose.yml`. `infra/.env` compose içindir; kök `.env` yerel
  `make dev` içindir. İkisini ayrı tut; LAN IP gibi ortak değerleri iki dosyada da güncelle.
- API container entrypoint'i `alembic upgrade head` çalıştırır. Migration'lar
  `apps/api/alembic/versions/` altında sıralıdır (son: `0027_finding_confidence_basis`);
  yeni migration'ı bu zincire `down_revision` ile bağla. Container'da elle alembic çalıştırma.
- Kod değişince: `make docker-rebuild` veya hedefli
  `docker compose -f infra/docker-compose.yml build api worker web && ... up -d --no-build`.
- Docker registry DNS'i bu makinede bayatlayabilir: `auth.docker.io ... no such host`.
  Fallback: `DOCKER_BUILDKIT=0 docker compose -f infra/docker-compose.yml build api worker web`.
- Tek giriş `https://<host>:8443` (Caddy internal CA); `http://localhost:8083` yalnız loopback.
  `8005` publish edilmez. Basic auth kullanıcısı `analyst`; parola
  `infra/.secrets/caddy-basic-auth.txt` (0600, gitignored) — sohbete/repoya yazma.
- LAN IP değişince **iki** env dosyasında `SHOWIS_TLS_HOSTS` ve `SHOWIS_DEFAULT_SNI`
  güncelle, sonra `docker compose -f infra/docker-compose.yml up -d --no-build --force-recreate caddy`.
- Caddy kök sertifika kurulumu container'da `certutil` yoksa başarısız olur; tarayıcı
  uyarısı normaldir.
- `USE_CELERY=1` varsayılan: taramalar worker'da koşar. API yalnız araştırma ajanını yürütür.

## Ürün invariant'ları

- Yalnız herkese açık kaynaklar. Giriş duvarı, CAPTCHA, erişim kontrolü veya robots ihlali
  ekleme. Kullanıcının açıkça verdiği seed/havuz URL'si tek istisnadır (robots atlanabilir).
- AI'ın havuza yazdığı kayıtlar `proposed` (araştırma) veya `candidate` (tam ajan) olur;
  kullanıcı onayı olmadan `confirmed` yazılmaz. Bu kuralı bozan değişiklik yapma.
- AI sağlayıcı sırları Fernet ile şifrelenir; base_url allowlist ve redaksiyon korunur.
- Kimlik zinciri (`services/chain.py`) yalnız herkese açık profil API'leri ve sayfaları
  okur; keşfedilen sayfada robots korunur (yalnız çapa = kullanıcının verdiği URL istisna).
  Kenar ağırlıkları/eşikler `chain.py` başındaki sabitlerdir; değiştirirsen
  `apps/api/tests/test_chain.py` yanlış pozitif senaryolarını (kurum hesabı, platform
  alt bilgisi, farklı isimli aynı kullanıcı adı) koru.
- E-posta ihlal kontrolü anahtarsız zincirle çalışır (XposedOrNot + Hudson Rock; HIBP anahtarı
  isteğe bağlı ek kaynak); hepsi başarısızsa `502`. Üçüncü kişilerin düz metin parolası
  getirilmez/saklanmaz. Anahtarı repoya yazma.
- Kullanıcıya görünen metinler ve yorumlar Türkçe; kod kimlikleri/API alanları İngilizce.

## Git / uzak depo

- Uzak depo `l1secx/showis` (public; `git remote` adı `l1secx`). Eski `l1secc/showis`
  artık yok; `origin` remote'u kaldırıldı.
- `gh` aktif hesabı `l1secx`; ek token veya `http.extraheader` gerekmez.
- Yeni remote yoksa: `git remote add l1secx https://github.com/l1secx/showis`.
- Push (özellikle `main`) kullanıcı istediğinde yapılır; commit'ler yerelde kalır.

## Bu ağa özgü not

- Doğrudan DuckDuckGo (`lite.duckduckgo.com`) bu ağda timeout verir. Arama zinciri Jina Reader
  üzerinden DDG Lite'a düşer (`packages/connectors/showis_connectors/websearch/jina.py`).
  Sıfır arama sonucu görürsen önce Jina/çıkış ağını kontrol et, sağlayıcı kodunu değiştirme.
