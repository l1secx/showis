# Uyum ve Operasyon Sorumluluğu

Bu belge, Showis'in kamuya açık kaynak taramasında **robots.txt**, üçüncü taraf
site koşulları (ToS) ve **hukuki dayanak** konularında neyi otomatik yaptığını,
neyin operatör sorumluluğunda kaldığını açıklar.

> Kısa özet: Yazılım teknik kontrolleri uygular; **hukuki dayanağın seçimi,
> yetki/izin ve site koşullarının kabulü operatörün sorumluluğundadır.**
> Yeni vaka formundaki onay kutusu bu sorumluluğun kayda geçtiği andır.

## 1. Hukuki dayanak (legal basis)

Her vaka oluşturulurken şu dayanaklardan biri seçilir:

| Değer | Anlamı | Tipik kullanım |
|---|---|---|
| `self_check` | Kişinin kendi hesabı/verisi | Kendi dijital ayak izini ölçmek |
| `consent` | Açık rıza | Kişinin açıkça izin verdiği inceleme |
| `legitimate_interest` | Meşru menfaat | Hukuken değerlendirilmiş, orantılı inceleme |

Kurallar:

- **Amaç sınırlaması:** Vaka amacı dışında veri toplanmaz/kullanılmaz.
- **Veri minimizasyonu:** Yalnız kamuya açık profil/URL/kanıt metaverisi tutulur;
  parola, özel mesaj, giriş gerektiren içerik toplanmaz.
- **Saklama:** `retention_days` (1–365) ile sınırlıdır; süre dolunca kanıt ve
  gözlemler temizlenir. Vaka silme kaskad temizlik yapar.
- **Veri sahibi hakları:** Erişim/silme/düzeltme talepleri operatör tarafından
  yanıtlanır; `GET /cases/{id}/export` ve `DELETE /cases/{id}` araçları vardır.
- **Özel nitelikli veri:** Sağlık, siyaset, din, cinsel yönelim gibi veriler
  hedeflenmez; hassas veri tespit edilirse çalışma durdurulur.
- **Reşit olmayan hedef:** `guard_target` ve inceleme bayrağı ile engellenir;
  operatör bu uyarıyı aşamaz.

Hukuki dayanak seçmek tek başına yeterli değildir; **gerçekten yetkiniz/izniniz
olmalıdır**. Yazılım bunu doğrulayamaz.

## 2. robots.txt

`robots.txt`, sitenin otomatik istemcilere hangi yolları tarayabileceğini
bildirdiği standarttır (RFC 9309).

Showis'in yaptığı:

- `HttpClient` her site için `robots.txt` okur ve izin verilmeyen yolları atlar.
- `robots.txt` engellerse sonuç `skipped` olur; "hesap yok" sayılmaz.
- Site-check varsayılanı **robots'a uymaktır**; `skip_robots=True` yalnız
  herkese açık, oturumsuz API uçları için bilinçli istisnadır.
- WMN alt kümesindeki tüm siteler varsayılan `skip_robots=False` ile gelir.
- `/health`, `/healthz` gibi kendi uygulama uçlarımız robots kapsamı dışındadır.

Operatör sorumluluğu:

- robots.txt yasağı bir **teknik** sınırdır; yasal izin anlamına gelmez.
- Site robots'u değiştirebilir; tarama sırasında güncel sonuç esas alınır.
- robots.txt'yi aşmak için vekil/proxy, IP rotasyonu, CAPTCHA çözümü veya
  kimlik doğrulama duvarı aşımı **kullanılmaz**.

## 3. Üçüncü taraf kullanım koşulları (ToS)

ToS, sitenin kullanıcılarıyla yaptığı sözleşmedir; otomatik erişim, veri
çekme, önbellekleme ve saklama koşullarını içerebilir.

Showis'in yaptığı:

- Yalnız **herkese açık** sayfalar/API'ler kullanılır.
- Giriş duvarı, ödeme duvarı, CAPTCHA, erişim kontrolü aşılmaz.
- Site başına istek hızı sınırlandırılır (`SHOWIS_HOST_MAX_CONCURRENCY`,
  `SHOWIS_HOST_MIN_INTERVAL`, robots ve site-check cache).
- `403/429` yanıtları "hesap yok" sayılmaz; `unknown` olarak işaretlenir.
- Kaynak ve lisans bilgisi kanıt zincirinde/raporda tutulur.

Operatör sorumluluğu:

- Her kaynak için güncel ToS'yi okumak ve kabul etmek.
- ToS otomatik erişimi yasaklıyorsa o kaynağı **kullanmamak**.
- Ticari kullanım, yeniden yayınlama veya veri seti oluşturma gibi durumlarda
  sitenin lisansını ayrıca kontrol etmek.
- ToS değişikliklerini takip etmek.

## 4. Ücretsiz API'ler ve kaynak lisansları

| Kaynak | Erişim | Not |
|---|---|---|
| GitHub REST | Anahtarsız (düşük limit) / token | Public API; token önerilir |
| GitLab, Keybase, Reddit, HN, npm, PyPI, HuggingFace, Bluesky | Anahtarsız public API | Site/API ToS geçerlidir |
| Hacker News Algolia | Anahtarsız | Etkinlik zenginleştirme |
| OpenAlex | Anahtarsız (düşük günlük kota) | `SHOWIS_CONTACT_EMAIL` ile polite pool |
| Gravatar | Anahtarsız | E-posta hash'i; açık profil JSON |
| WhatsMyName | CC BY-SA 4.0 | `sitecheck/WMN_NOTICE.md`; 28 sitelik alt küme |
| Web arama sağlayıcıları | DDG anahtarsız; Brave/SerpAPI/CSE anahtarlı | Sağlayıcı ToS ve kotaları |

Yeni bir kaynak eklenirken: lisans, robots, ToS, kimlik doğrulama gereksinimi,
kişisel veri riski ve rate limit değerlendirilmelidir.

## 5. Operatör kontrol listesi

1. Hedef ve amaç net mi? Hukuki dayanak gerçekten var mı?
2. Yalnız kamuya açık kaynak mı taranıyor? Giriş/CAPTCHA aşımı yok mu?
3. İlgili sitelerin robots.txt ve ToS'si kabul edilebilir mi?
4. Veri minimizasyonu ve saklama süresi uygun mu?
5. Üçüncü taraf API anahtarları güvenli mi (env/secret, repoda değil)?
6. Edge erişimi korumalı mı (basic auth / TLS / ağ erişimi)?
7. Veri sahibi talebi ve silme prosedürü hazır mı?
8. Olay/şüphe durumunda taramayı durdurma ve kayıtları koruma planı var mı?

## 6. İzleme, dışa aktarma ve kanıt

- **Otomatik izleme:** Vaka sahibi izlemeyi açarsa yalnız aynı vaka kapsamı ve
  aynı hukuki dayanakla yeniden tarama yapılır. Aralık ve mod operatör tarafından
  seçilir; `monitor_last_scan_at` audit izi taşır.
- **Webhook:** `scan.completed` özeti gönderilir. URL'ler SSRF korumasından geçer;
  iç ağ/localhost hedefleri reddedilir.
- **STIX/Maltego:** Dışa aktarım yalnız kamuya açık entity/bulgu/kanıt metaverisini
  taşır; ham kanıt içeriği gönderilmez.
- **Ekran görüntüsü:** Yalnız bulgu URL'si için, robots.txt ve SSRF kontrolleri
  altında alınır; kanıt hash'i ile saklanır. Giriş duvarı/CAPTCHA aşılmaz.

## 7. Kullanıcı, rol ve sahiplik

- Kullanıcılar `api_users` tablosunda tutulur. Her kullanıcının yalnız
  SHA-256 hash'i saklanan kişisel bir API anahtarı vardır.
- Roller:
  - `admin`: tüm vakaları okur/yazar, kullanıcı ve sağlayıcı ayarlarını yönetir.
  - `analyst`: vaka oluşturur; sahibi olduğu veya sahipsiz vakaları yazar.
  - `viewer`: yalnız okur; yazma istekleri **403** döner.
- `SHOWIS_API_TOKEN` paylaşılan token'ı geriye uyum için **admin** sayılır.
  Üretimde kişisel anahtarlara geçilip paylaşılan token devre dışı bırakılmalıdır.
- Kişisel anahtar tarayıcıda `localStorage` içinde tutulur ve Next proxy
  üzerinden `X-User-Token` başlığıyla taşınır. XSS yüzeyi nedeniyle CSP ve
  güvenilir istemci zorunludur.
- Vaka `owner` alanı analyst için yetki sınırıdır; `admin` her vakaya erişir.
- Kullanıcı oluşturma: `POST /api/v1/auth/users` (admin) veya Ayarlar ekranı.
  Token yalnız oluşturma yanıtında bir kez gösterilir.
- Edge basic auth hâlâ tek kullanıcıdır; kişisel API anahtarı ikinci katmandır.
  Gerçek çok kullanıcılı edge için Caddy kullanıcıları ayrıca yönetilmelidir.

## 8. Canlı ortam notları

- API `X-API-Token` ile korunur; edge basic auth için
  `SHOWIS_CADDY_USER` + `SHOWIS_CADDY_PASSWORD_HASH` tanımlanmalıdır.
- TLS yoksa `:8083` düz HTTP'dir; üretimde TLS sonlandırma ve host allowlist
  (`SHOWIS_ALLOWED_HOSTS`) önerilir.
- Kanıt ve raporlar `umask 077` ile 0600/0700 izinlerine sahiptir.
- DB migration'ları yedek sonrası `alembic upgrade head` ile uygulanır.
