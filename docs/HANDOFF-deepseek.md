# Handoff — Showis geliştirme notları

Bu not, aynı depoda çalışan ikinci asistan (freebuff/deepseek) için yazıldı.
Amaç: **isim + soyisim (+ okul) verildiğinde anlamlı sonuç üretmek**, sonuçları
Bulgular sekmesine akıtmak, tarama süresini önceden tahmin etmek ve arayüzü
gerçek bir OSINT ürünü gibi göstermek.

## Bu turda ne değişti

### Backend

| Dosya | Değişiklik |
| --- | --- |
| `packages/connectors/showis_connectors/websearch/connector.py` | Yeni `WebSearchConnector`: hedef ad + ipuçları (okul/şehir/şirket) ile web araması yapar, her sonucu `Entity`'ye çevirir (`name`, `search_title`, `search_snippet`). Tier C olduğu için yalnızca `search` görevi üretir. |
| `apps/api/showis/services/connectors_factory.py` | `WebSearchConnector` listenin başına eklendi — hesap bulunamasa bile isim araması bulgu üretir. |
| `packages/engine/showis_engine/resolver.py` | İsim skoru artık arama sonucu başlığını da kullanır (`search_title`); okul ipucu bio/snippet içinde geçerse +0.05; yeni `LEAD` statüsü (0.5 altı ama isim/konum sinyali olan sonuçlar). |
| `packages/engine/showis_engine/scan_modes.py` | O1/O2/O3 profilleri (görev/istek/duvar saati/varyant sayısı) + `estimate_scan()` süre tahmini + `task_allowed()/variant_cap()`. |
| `apps/api/showis/services/scanner.py` | (a) Moda göre connector eleme, (b) **drain loop**: connector'lar çalışırken eklediği görevler de koşar, (c) bütçe/zaman aşımında kalan görevler `skipped` işaretlenir, (d) **artımlı skorlama**: her 10 görevde bulgular Bulgular sekmesine düşer, (e) LLM açıklaması yalnızca aday+ bulgular için ve en fazla 12 tanesi için yapılır (her çağrı ~6 sn). |
| `apps/api/showis/api/v1/cases.py` | `POST /cases/{id}/scan` artık moda göre bütçe kuruyor; yeni uç: `GET /cases/{id}/scan/estimate?mode=O2`. |
| `packages/connectors/showis_connectors/websearch/duckduckgo.py` | Reklam/yonlendirme (`google.com/goto`, `duckduckgo.com/y.js`) sonuçları elenir — bulgulara çöp URL düşmüyordu. |

### Frontend (`apps/web`)

- `tailwind.config.js` + `globals.css`: slate/blue ölçekleri yeniden tanımlandı (cyan aksan), kart/buton/rozet/`input` bileşen sınıfları, ızgara arka plan, Inter + JetBrains Mono.
- `app/cases/[id]/scan-context.tsx`: **sekme gezinmesinde state kaybını çözen** `ScanSessionProvider` (SSE + polling, sessionStorage ile `scanId`, mod, bulgu filtresi korunur).
- `app/cases/[id]/panels.tsx`: O1/O2/O3 seçimi + "tahmini süre / görev / istek" kartı, canlı olay akışı, ilerleme çubuğu, filtreli Bulgular.
- `app/cases/new/page.tsx`: hedef-öncelikli form, "Oluştur ve tara" akışı (`/cases/{id}/scan?start=1`).

## Doğrulama

```bash
make test                      # 144 test
cd apps/web && npm run build   # tip kontrolü + build
cd infra && docker compose build api worker web && docker compose up -d
```

Canlı doğrulama (isim + okul):

```bash
curl -s localhost:8005/api/v1/cases/<id>/scan/estimate?mode=O2
curl -s -X POST localhost:8005/api/v1/cases/<id>/scan -H 'Content-Type: application/json' -d '{"mode":"O2"}'
curl -s localhost:8005/api/v1/cases/<id>/findings
```

Ölçülen referans (Kerem Boz + ODTÜ, O2): ~10 dk, ~140 görev, ~41 bulgu.
Tahmin: 688 sn / 139 görev — aynı mertebede.

## Açık işler (öncelik sırası)

1. **Görevler sıralı çalışıyor.** Bağımsız API görevleri `asyncio.gather` + rate-limit ile paralelleştirilirse O2 ~10 dk'dan ~3 dk'ya iner. En yüksek kazanç burada.
2. **Süre tahmini tek ölçüme dayanıyor.** `_COST` sabitleri `packages/engine/showis_engine/scan_modes.py` içinde; gerçek görev sürelerini `tasks.finished_at - created_at`'tan toplayıp kalibre edilmeli.
3. ~~`browser` servisi crash-loop~~ **ÇÖZÜLDÜ (2026-11-15)**: kök neden `packages/engine/showis_engine/links.py:17` `from PIL import Image` idi ama `packages/engine/pyproject.toml` pillow'ı bildirmiyordu; `Dockerfile.api`/`Dockerfile.worker` elle pillow kurup gizliyordu, `Dockerfile.browser` kurmuyordu. `pillow>=10.0.0` engine bağımlılıklarına eklendi, `browser` yeniden derlendi. Doğrulama: `POST http://browser:8081/render {"url":"https://example.com"}` → 200 + HTML.
4. **Reddedilen bulgular gürültülü**: yalnızca username eşleşmesi 0.30 skorla `rejected` düşüyor ve listede görünüyor. Bulgular varsayılan filtresi "aday ve üzeri" olabilir.
5. **Planner çıktısı hâlâ kullanılmıyor**: `QueryPlanner.plan()` sonuçları loglanıyor, connector sorgularına girmiyor. LLM varsa buradan gelen sorgular `WebSearchConnector`'a beslenmeli.
6. **sitecheck** 48 site × kullanıcı adı; tek görevde seri kalıyor. Kullanıcı adı başına paralel görev veya sonuç önbelleği (aynı kullanıcı adı tekrar taranmasın) gerekli.

## Dokunmadan önce sor

- `packages/contracts/showis_contracts/enums.py` (`FindingStatus`) ve resolver skor ağırlıkları: API/DB şeması ve tüm testler bunlara bağlı.
- Tier politikası (`packages/engine/showis_engine/policy.py`): Tier C connector yalnızca arama görevi üretebilir; bu kural güvenlik sınırı.
