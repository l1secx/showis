# Showis OSINT — Gizlilik ve Veri İşleme Politikası (KVKK / GDPR)

## 1. Veri İşleme İlkeleri
Showis, 6698 sayılı Kişisel Verilerin Korunması Kanunu (KVKK) ve ilgili uluslararası mevzuat doğrultusunda şu ilkeleri esas alır:
- **Hukuka ve dürüstlük kurallarına uygun olma:** Her vaka oluşturulurken hukuki dayanak (`consent`, `legitimate_interest`, `self_check`) zorunlu olarak kaydedilir.
- **Doğru ve gerektiğinde güncel olma:** Veriler kaynak URL ve zaman damgasıyla eşleştirilerek saklanır.
- **Belirli, açık ve meşru amaçlar için işlenme:** Yalnızca hedef araştırma görevine hizmet eden açık profil bilgileri işlenir.
- **İşlendikleri amaçla bağlantılı, sınırlı ve ölçülü olma (Veri Minimizasyonu):** Sadece ad, kullanıcı adı, kamuya açık sosyal bağlantılar, biyografi ve lokasyon gibi göreve doğrudan yarayan alanlar toplanır.

## 2. Veri Sahibi Hakları ve Silme
- **Kaskad Silme:** `DELETE /cases/{id}` çağrıldığında vaka, hedefler, toplanan ham gözlemler, kanıtlar (MinIO dahil) ve bulgular kalıcı olarak silinir.
- **Veri Taşınabilirliği / Dışa Aktarma:** `GET /cases/{id}/export` ile veri sahibine ait tüm toplanmış veriler standart JSON formatında teslim edilebilir.
- **Audit Kayıtları:** Sistemde gerçekleştirilen her dış sorgulama, IP ve connector bazında `audit_logs` tablosunda saklanır.

## 3. Üçüncü Taraf İşleyiciler (dışa aktarım)

Bir tarama sırasında **hedefe ait veri** (kullanıcı adı, ad-soyad, ipucu metni ve
vaka için girilmişse e-posta) aşağıdaki dış servislere gönderilir. Amaç yalnızca o
vakanın kimlik doğrulama görevidir; hiçbir sağlayıcıya "veri satışı" yapılmaz.

| Sağlayıcı | Giden veri | Amaç | Not / sınırlama |
|---|---|---|---|
| GitHub API (`api.github.com`) | kullanıcı adı, sorgu metni, **token varsa** e-posta (`search/commits?q=author-email:`) | profil/commit keşfi, e-posta→login pivotu | E-posta commit araması **yalnız** yapılandırılmış token varken çalışır; yoksa görev üretilmez. E-posta, log ve kanıt URL'sinde maskelenir (`email_tag`) |
| Keybase (`keybase.io`) | kullanıcı adı veya **GitHub login** | proof graph (cross-link kanıtı) | Keybase API e-posta ile arama desteklemez; e-posta seed'i doğrudan gönderilmez |
| Gravatar (`gravatar.com`) | e-posta **hash'i** (MD5) | avatar/profil tespiti | Ham e-posta gönderilmez, yalnız hash |
| Web arama sağlayıcıları (SerpAPI / DuckDuckGo / Brave, yapılandırılan) | ad-soyad, kullanıcı adı, ipucu (şehir/okul/şirket) | açık kaynak taraması | Sağlayıcı anahtarı ayarlardan gelir; anahtar yoksa ilgili görev üretilmez |
| Site-check hedefleri (kamuya açık yüzlerce profil host'u) | **yalnızca kullanıcı adı** (URL yolunda) | kullanıcı adı varlık kontrolü | İstek sayısı mod başına bütçeyle sınırlıdır (`SHOWIS_SITECHECK_REQUEST_BUDGET`); NSFW kategorisi varsayılan olarak elenir |
| LLM sağlayıcısı (`opencode.ai`, yapılandırılmışsa) | bulgu alanları (platform, kullanıcı adı, skor dökümü, kanıt özeti) | açıklama metni üretimi | Sağlayıcı yapılandırılmamışsa deterministik yerel açıklama kullanılır; API anahtarı log'a yazılmaz |

Ek kurallar:
- Her dış istek `audit_logs`'a ve connector bazlı sayaçlara yazılır; kanıtlar vaka
  saklama süresi (`retention_days`) sonunda silinir.
- Sağlayıcı hataları (rate limit, 403) taramayı durdurmaz; ilgili görev "hata"
  olarak işaretlenir ve diğer connector'lar devam eder.
- Parola-sıfırlama uçlarıyla hesap yoklama, breach/telefon verisi ve captcha
  bypass bu ürünün kapsamı **dışındadır**.

