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

## Final kapı (tek komut, go/no-go)

```bash
make gate                             # = bash scripts/final_gate.sh
bash scripts/final_gate.sh --live     # + canlı uçtan uca kabul (--strict)
bash scripts/final_gate.sh --quiet    # yalnız özet tablosu
```

Adımlar: ruff · mypy · pytest · web `tsc --noEmit` · web `npm run build` ·
`make schemas` + `types.ts` drifti · `docker compose config -q` · sır taraması
(`--live` ile canlı kabul).

Her adım ayrı ölçülür ve `ADIM | SÜRE | SONUÇ` tablosu basılır; kapanış:
`SONUÇ: HAZIR` (exit 0) veya `SONUÇ: HAZIR DEĞİL (N hata)` (exit 1). Tek hata
script'i durdurmaz (`set -uo pipefail`), böylece tüm tablo tek koşuda görünür.
`UYARI` çıkış kodunu etkilemez (ör. commit edilmemiş `types.ts` veya eksik ortam
değişkeni). `make schemas` çalıştırıldıktan sonra `apps/web/lib/types.ts`
değişmemelidir (T-334: üretilen tipler `types.generated.ts`'e gider); ihlalde
dosya geri alınır ve adım FAIL olur. Süre ~40 sn (build cache'li).

## Uçtan uca kabul testi (tek komut)

```bash
export SHOWIS_API_TOKEN=...                  # API token (T-108)
export SHOWIS_BASIC_AUTH_USER=showis         # edge basic auth (T-113)
read -rs SHOWIS_BASIC_AUTH_PASSWORD && export SHOWIS_BASIC_AUTH_PASSWORD
bash scripts/e2e_acceptance.sh               # ~2 dk: vaka → O1 tarama → bulgular → rapor → grafik → export → silme
```

20+ kontrolü sırayla koşar: health, 401/200 kimlik katmanı, vaka, tahmin, tarama, 409 idempotans,
bulgular (+`authenticity`/`authenticity_reasons`, `min_authenticity` ve `status` filtreleri),
bulgu `confirmed`, JSON rapor bölümleri (5), HTML rapor + CSP, rapor dosyası/izin, export,
kimlik grafiği (`edges`), `/healthz`, `/metrics`, güvenlik başlıkları, `backfill_edges --dry-run`,
silme + rapor kalıntısı. Her adım `PASS/FAIL/BEKLENIYOR/UYARI/ATLANDI` basar; sonunda
`SONUÇ: GEÇTİ/KALDI` + `exit 0/1` (`UYARI` çıkış kodunu etkilemez).

Deploy öncesi / sonrası:

```bash
bash scripts/e2e_acceptance.sh --pre-deploy   # deploy ÖNCESİ: yeni kabiliyetler BEKLENIYOR sayılır
bash scripts/e2e_acceptance.sh --strict       # deploy SONRASI kabul kapısı: hiçbir adım BEKLENIYOR kalamaz
```

`--keep` vakayı silmeden bırakır; `--base-url/--token/--basic-auth` ile kimlik verilebilir.
`--strict` ve `--pre-deploy` birlikte kullanılamaz. Kimlik bilgileri `curl --config` ile
verilir, `ps`/argv'de görünmez. Script yalnız kendi oluşturduğu `E2E …` vakasını siler.
`backfill_edges` adımı DB'ye `docker inspect showis-postgres` ile ulaşır (`--dry-run`, yazmaz);
erişim yoksa adım `BEKLENIYOR` olur.

## Retention

- `evidence.expires_at` = oluşturma + `case.retention_days` (varsayılan 30 gün)
- Celery beat günlük `showis.retention.purge` çalıştırır: süresi dolan evidence + observation + 365 günden eski audit log silinir
- `DELETE /cases/{id}` tüm veriyi kaskad siler
- Manuel tetikleme: `celery -A showis.services.worker.celery_app call showis.retention.purge`

## Yedekleme / Geri yükleme (T-315)

### Ne, nerede, ne sıklıkta
| | |
|---|---|
| Konum | `data/backups/` — dizin **700**, dosyalar **600** (kişisel veri) |
| Veritabanı | `showis-YYYYmmdd-HHMM.dump` — `pg_dump -Fc` (custom format, sıkıştırmalı) |
| Rapor/kanıt dosyaları | `showis-YYYYmmdd-HHMM.files.tar.gz` — `report_data` volume'undan `reports/` + `evidence/` |
| Bütünlük | Her dosya için `<ad>.sha256` |
| Sıklık | **Saatlik** — systemd user timer `showis-backup.timer` (`OnCalendar=hourly`, `Persistent=true`, `RandomizedDelaySec=120`) |
| Boyut sınırı | Toplam **2 GB**; aşılırsa en eski yedekler budanır (en yenisi asla silinmez) |

MinIO **kullanılmıyor** (kodda referans yok); kanıt dosyaları yerel diskte / named volume'da
tutulur, bu yüzden MinIO mirror'u yedek kapsamında değil.

### Retention politikası
- Son **7 gün**: her gün için **en yeni** yedek (7 dosya).
- Son **4 hafta**: pazartesi yedekleri (bir pazartesi hem günlük hem haftalık sayılır, tek kopya).
- Daha eskiler silinir. Silme yalnız `data/backups/` içinde ve `showis-*` desenine uyan dosyalarda.

### Komutlar
```bash
# Elle yedek (timer'ı beklemeden)
systemctl --user start showis-backup.service
journalctl --user -u showis-backup.service -n 30 --no-pager

# Timer durumu
systemctl --user list-timers | grep showis

# Yedek listesi
ls -la data/backups

# DOĞRULAMA TATBİKATI (canlı DB'ye dokunmaz)
bash infra/scripts/backup-verify.sh
#  -> sha256 + pg_restore --list
#  -> showis_restore_test veritabanına restore
#  -> tablo satır sayıları canlı ile karşılaştırılır
#  -> test DB silinir; fark varsa exit != 0
```

### Felaket kurtarma (canlı DB kayboldu/bozuldu)
1. **Servisi durdur** (yazmayı kes): `docker compose -f infra/docker-compose.yml stop api worker beat`
2. Yedeği doğrula: `bash infra/scripts/backup-verify.sh data/backups/showis-<ts>.dump`
   (tatbikat yeşilse devam).
3. Geri yükle (onay ister, DB adını yazmanız gerekir):
   `bash infra/scripts/restore.sh --force-live data/backups/showis-<ts>.dump`
4. Kanıt dosyalarını geri aç (gerekliyse):
   `docker run --rm -v infra_report_data:/dst -v "$PWD/data/backups:/src:ro" \
      alpine:3.20@sha256:d9e853e8… sh -c 'tar -xzf /src/showis-<ts>.files.tar.gz -C /dst'`
5. Servisleri başlat: `docker compose -f infra/docker-compose.yml up -d api worker beat`
6. Doğrula: `/health` 200, `GET /api/v1/cases` beklenen vaka sayısı, `data/backups` son yedek yaşı < 1 saat.

> Dikkat: `restore.sh` varsayılan olarak **test DB**'ye yükler; canlıya yazmak için
> `--force-live` şart. `--clean --if-exists` yalnız bu modda kullanılır.

### Geri alma / kapatma
```bash
systemctl --user disable --now showis-backup.timer   # saatlik yedeklemeyi kapat
systemctl --user stop showis-backup.service
```

## Üretim öncesi kontrol listesi (T-336)

Her madde: **neden gerekli** + tek satırlık doğrulama/uygulama komutu. Şu an bu makinede
işaretli olmayanlar = üretime çıkmadan kapatılmalı.

| # | Madde | Neden | Komut |
|---|---|---|---|
| 1 | **TLS** — şu an düz HTTP `:8083` | Basic auth + API token düz metin gider; LAN'da dinlenebilir | `curl -sI http://127.0.0.1:8083/ \| head -1` → `http`. Geçiş: Caddyfile site adresini `showis.example.com` yap (Caddy otomatik ACME) veya iç CA: `docker exec showis-caddy caddy trust` |
| 2 | **Disk şifreleme (LUKS)** — yok | Vaka/delil PII diskte düz; cihaz kaybı/hırsızlık = veri ifşası | `lsblk -f \| grep -i crypt` (boşsa şifreli değil). Kurulum: `cryptsetup luksFormat /dev/nvme0n1pX` + `/etc/crypttab` |
| 3 | **`MASTER_KEY` rotasyonu** | Sağlayıcı anahtarları Fernet ile şifreli; anahtar sızarsa hepsi açılır; şu an rotasyon yok | Doğrulama: `docker exec showis-api sh -lc 'echo ${#MASTER_KEY}'` (>0). Rotasyon: yeni anahtarla `settings` kayıtlarını yeniden şifrele (API `PUT /api/v1/settings/...`), eski anahtarı 24 saat paralel tut |
| 4 | **`fail2ban` + `auditd`** — host'ta kapalı | SSH/Caddy brute-force ve sistem çağrısı denetimi yok | `systemctl is-active auditd fail2ban` → `inactive`. Kur: `sudo systemctl enable --now auditd fail2ban` |
| 5 | **Port publish politikası** | Docker publish edilen portlar UFW'yi **atlar**; yalnız `8083` açık olmalı | `ss -ltnp \| grep -E ':8083\|:8005\|:5432'` (yalnız 8083). Sıkılaştır: `ports: ["127.0.0.1:8083:8083"]` veya tailscale-only |
| 6 | **`SHOWIS_ALLOWED_HOSTS`** | Host allowlist kapalıysa DNS-rebinding ile edge kontrolü aşılabilir | `docker logs showis-caddy 2>&1 \| grep "Host allowlist"`. Aç: `infra/.env` → `SHOWIS_ALLOWED_HOSTS=localhost,127.0.0.1,l1sec,<LAN-IP>` + `docker compose up -d --no-deps caddy` |
| 7 | **`SHOWIS_ALLOWED_ORIGINS`** | CORS varsayılanı yalnız localhost; üretim alan adı eklenmezse UI kırılır | `curl -sI -u showis:*** -H 'Origin: https://showis.example.com' http://127.0.0.1:8083/api/v1/cases \| grep -i access-control` |
| 8 | **Yedek doğrulama periyodu** | Yedek var ama geri yüklenebilirliği test edilmiyor (T-315 tatbikatı elle) | `bash infra/scripts/backup-verify.sh` (aylık): `systemctl --user list-timers \| grep backup` |
| 9 | **Zorunlu env'ler dolu** | `POSTGRES_PASSWORD`, `MINIO_ACCESS_KEY/SECRET`, `MASTER_KEY`, `SHOWIS_API_TOKEN` boşsa compose fail-fast eder | `cd infra && docker compose config -q` (hata yoksa tamam) |
| 10 | **Bağımlılık/sır kapıları** | Pinli lock + CVE/sır taraması CI'da | `bash infra/scripts/check-secrets.sh` · `pip-audit -r infra/requirements.lock` |
| 11 | **Uzak yedek kopya** | Yedekler aynı diskte (`data/backups`); disk arızası ikisini de götürür | `rclone sync data/backups remote:showis-backups` (veya harici disk) |

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
