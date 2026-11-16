# Showis Runbook

## Mimari

| Servis | Port | Açıklama |
|---|---|---|
| caddy | 8080 | TLS + reverse proxy (web + api) |
| web | 3000 | Next.js arayüzü |
| api | 8000 | FastAPI + SSE |
| worker | — | Celery worker (`osint` kuyruğu) |
| beat | — | Celery beat (günlük retention purge) |
| browser | 8081 | Playwright render servisi (`POST /render`) |
| postgres | 5432 | Ana veritabanı |
| redis | 6379 | Celery broker |
| minio | 9000/9001 | Kanıt nesne deposu (S3 API) |

## Çalıştırma

```bash
cp .env.example .env   # MASTER_KEY üret: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
make docker-up         # veya: docker compose -f infra/docker-compose.yml up -d --build
make demo              # sentetik uçtan uca demo
```

Tarayıcıdan: `http://localhost:8083`

## Sağlayıcı anahtarları (BYOK)

Arayüz → **Ayarlar** ekranından girilir (GitHub PAT, Brave/SerpAPI/CSE, LLM). Anahtarlar `MASTER_KEY` ile Fernet şifrelenir, maskeli gösterilir. Anahtarsız da uygulama açılır; ilgili görevler `error`/`blocked` observation üretir.

## Tarama akışı

1. `POST /cases/{id}/scan` → Scan `queued` → `running`
2. Connector'lar `plan()` ile TaskSpec üretir, görevler `tasks` tablosuna yazılır
3. Her görev `run()` → `Observation`(lar); normalize → `Entity`/`Edge`
4. Resolver deterministik skorlar → `Finding` (candidate/confirmed/rejected)
5. `scan.completed` SSE ile yayınlanır

`USE_CELERY=1` ile taramalar Celery worker'a devredilir; aksi halde API içinde BackgroundTasks çalışır.

## Retention

- `evidence.expires_at` = oluşturma + `case.retention_days` (varsayılan 30 gün)
- Celery beat günlük `showis.retention.purge` çalıştırır: süresi dolan evidence + observation + 365 günden eski audit log silinir
- `DELETE /cases/{id}` tüm veriyi kaskad siler
- Manuel tetikleme: `celery -A showis.services.worker.celery_app call showis.retention.purge`

## Yedekleme / Geri yükleme

```bash
make backup    # infra/scripts/backup.sh -> backups/<timestamp>/
make restore   # infra/scripts/restore.sh <backup-dir>
```

## Sorun giderme

| Belirti | Kontrol |
|---|---|
| THM/HTB bulgu yok | `BROWSER_SERVICE_URL` erişilebilir mi? `GET http://browser:8081/health` |
| GitHub 403 | PAT girilmiş mi, rate limit dolmuş mu (`X-RateLimit-Remaining`) |
| Scan `failed` | `tasks.error` alanı; audit log `connector.task` kayıtları |
| SSE akmıyor | `/scans/{id}/events` açık mı, `scan.completed` geldi mi |
| PDF Türkçe bozuk | `fonts-dejavu-core` kurulu mu (api imajında var) |

## Güvenlik notları

- Tüm container'lar non-root, `read_only`, `cap_drop: ALL`, `no-new-privileges`
- `egress` ağı yalnızca worker/browser'da — API dışarı çıkamaz
- Yasak host'lar `policy.FORBIDDEN_HOSTS` ile runtime'da engellenir
- Log'larda sırlar `SecretRedactionFilter` ile maskelenir
