# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

**Birincil kullanıcı: OSINT analisti.** Kendi bürosunda veya küçük bir ekipte, kamuya açık
kaynaklardan bir kişinin dijital izini çıkarır; aday hesapları güven skoruyla sıralar,
kanıt toplar ve sonucu bir dosyaya dönüştürür.

**İkincil kullanıcı: kendi hesabını kontrol eden kişi** (`legal_basis: self_check`). Kendi
adına kayıt açan hesaplarının hangi platformlarda göründüğünü ölçmek ister. Bu kullanıcı
için hukuki dayanak zorunlu alan değildir — `self_check` bunu ayrı bir dayanak olarak taşır.

Analistin çalışma ortamı masaüstü tarayıcıdır; gün içinde kayıtları karşılaştırır, kanıt
bağlantılarını açar ve rapor üretir. Yoğun okuma yapar (güven skoru kırılımı, kanıt zinciri,
kronoloji), bu yüzden yoğunluğu ve okunabilirliği birlikte ister.

## Product Purpose

Showis, ad, soyad veya kullanıcı adından yola çıkarak kamuya açık kaynaklarda hesap tespiti
yapar; bulunan hesapları çapraz platform ilişkilendirmesiyle puanlar, her puanı geri
izlenebilir kanıtla destekler ve sonucu PDF/JSON/HTML raporuna dönüştürür.

Başarı ölçütü: analist "bu hesap aynı kişi mi" sorusunu, kanıt zincirini görerek ve her
bulguyu gerekçesiyle inceleyerek cevaplayabilmelidir. Kayıt sayısı ya da bulgu sayısı
değil, **kararın savunulabilir olması** başarıdır.

## Positioning

Ürünü ayıran mekanizma üç parçadan oluşur:

1. **Güven skoru kırılımı.** Her bulgu tek bir sayı değil, ağırlıklı sinyallerin
   (kullanıcı adı eşleşmesi, isim benzerliği, çapraz bağlantı, bio/konum/okul, commit
   e-postası, avatar hash, çelişki) toplamıdır ve kırılımı görünür. Karar, skora değil
   kırılıma bakılarak verilir.
2. **Kanıt zinciri.** Her bulgu, alındığı kaynak URL'si, içerik SHA-256 hash'i ve zaman
   damgasıyla saklanır. Rapor bu kayıtlardan üretilir.
3. **Hukuki dayanak kayda gömülüdür.** Her vakada dayanak (`consent`,
   `legitimate_interest`, `self_check`) ve saklama süresi zorunludur; silme kaskadı
   rapor klasörüne kadar çalışır.

Yalnızca herkese açık sayfalar ve API'ler taranır; giriş duvarı aşma, CAPTCHA bypass ve
unofficial API kullanımı kodda yoktur. Bu, "daha çok veri çekelim" ile "bu veri hukuka
uygun mu" arasındaki dengeyi ürünün içine gömüldüğü için benzer araçlardan ayrılır.

## Operating Context

- **Dağıtım:** self-hosted. Docker Compose ile API (FastAPI), web (Next.js), Postgres,
  Redis ve Caddy birlikte ayağa kalkar. Tek giriş noktası Caddy'dir; edge'de basic auth,
  API'de `X-API-Token` vardır.
- **BYOK:** GitHub, LLM (OpenAI uyumlu), Brave Search ve SerpAPI anahtarları kullanıcı
  tarafından girilir, sunucuda Fernet ile şifrelenir ve maskeli gösterilir.
- **Tarama modları:** O1 hızlı, O2 standart, O3 derin. Her modda tahmini süre, görev ve
  istek sayısı önceden hesaplanır ve arayüzde gösterilir — analist neyin ne kadar süreceğini
  başlatmadan önce bilir.
- **Canlı akış:** SSE ile görev ve olay akışı anında akar; tarama iptal edilebilir.
- **Rapor:** PDF, JSON ve HTML. HTML raporu bilerek sayfada render edilmez, yalnızca
  indirilir (XSS yüzeyi açmamak için).
- **Dil:** arayüz tamamen Türkçe. Hukuki ve operasyonel metinler bu dildir.

## Capabilities and Constraints

- Vaka yönetimi: sunucu taraflı arama, durum filtresi, sıralama ve sayfalama.
  100+ vakada arayüz zorlanmaz.
- Bağlayıcılar: 29 bağlayıcı (profil), 64 platform site kontrolü, hibrit arama zinciri
  (Brave → SerpAPI → Google CSE → DuckDuckGo), varyant motoru, paralel tarama ve bütçe
  kontrolü (fail-closed).
- **Araştırma havuzu:** Hedef, ipuçları, seed'ler, notlar, yorumlar, **tarihli
  olaylar** ve AI önerileri vaka içi tek havuzda; tekilleştirme, arama,
  kaynak/durum filtresi ve onay akışı. Olay kayıtları tarih/yer/kurum bağlamıyla
  tarama sorgularını ve kanıt zincirini yönlendirir.
- **Derin AI araştırma ajanı:** Çoklu OpenAI uyumlu sağlayıcı profili; model
  keşfi ve izinli model seçimi; hızlı/standart/derin bütçe profilleri; isim +
  kullanıcı adı kombinasyonları, platform sorguları ve linktree/about.me/beacons
  toplayıcıları; süpervizör dalgaları ve paralel alt ajanlar (sosyal eşleme,
  kimlik doğrulama, ihlal, zaman çizelgesi); sayfa içi profil takibi; kapsama
  doygunluğu veya bütçeye göre durma; havuz kanıtlarından yapılandırılmış
  biyografi; "araştır/öner" ve "tam ajan" modları; canlı aktivite, SSE ve
  kaynaklı nihai sentez.
- **HIBP ihlal kontrolü:** E-posta havuz kayıtları için ihlal adı, tarihi, sızan
  veri sınıfları ve açıklaması; sonuç kanıt kaydına işlenir.
- **Tedarik zinciri modülü:** Şirket, tedarikçi, ürün ve yönlü ilişkiler için ayrı
  düğüm/kenar modeli; Cytoscape grafiği ve AI önerisi onay akışı.
- Authenticity: hesap yaşı, aktivite, boş bio, varsayılan avatar ve cross-link tutarlılığı
  deterministik çözümleyiciyle skora katkı verir.
- **CSP `style-src 'self'`:** uzak font ve stil yüklemesi engellidir. Arayüz yazı tipleri
  sistem yığınından gelir; kendi barındırılan bir font çifti ancak CSP allowlist'i ile
  eklenebilir.
- **API tokenı yalnızca sunucuda:** `SHOWIS_API_TOKEN` istemciye hiçbir biçimde sızmaz.
- **Dokümantasyon uçları varsayılan kapalı** ve açıldığında da token ister.
- **Kanıt saklama:** KVKK uyumlu saklama süresi, kaskad silme, günlük yedek ve restore
  tatbikatı.
- **Doğrulanmamış:** müşteri logoları, referanslar, fiyatlandırma veya kullanım
  istatistikleri — arayüzde bunlara yer verilmemelidir.

## Brand Commitments

- **Ad:** Showis. Değiştirilmedi ve değiştirilmesi önerilmiyor.
- **Dil:** Türkçe. Arayüz metinleri düz, etkin çatıda ve teknik değil; kullanıcının ne
  yapacağını söyler, nasıl çalıştığını anlatmaz.
- **İşaret:** nişangâh (crosshair) — hedef tespiti. `apps/web/app/icon.svg`.
- **Tutum:** abartısız. Kanıt, skor ve kaynak öne çıkar; süsleme ve heyecan dili yoktur.

## Evidence on Hand

- `packages/engine/showis_engine/report/` — Jinja2 rapor şablonları (PDF/HTML) ve
  yönetici özeti bölümleri; arayüzdeki "Yönetici özeti" panelinin veri şekli buradan gelir.
- `apps/web/app/cases/[id]/platform-meta.ts` — 60+ platformun etiket ve marka rengi;
  arayüzdeki platform işaretlerinin tek kaynağı.
- `packages/engine/showis_engine/resolver.py` — sinyal ağırlıkları; skor kırılımındaki
  etiketler ve ipuçları buradan türetilir.
- `docs/legal/` — uyum matrisi, AUP, saklama ve gizlilik belgeleri; arayüzdeki yasal
  çerçeve metinlerinin kaynağı.
- `README.md`, `docs/runbook.md`, `docs/ROADMAP.md` — ürün davranışının doğrulanabilir
  açıklaması.
- **Eksik olanlar:** müşteri referansı, kullanım metriği, fiyat, basın alıntısı. Arayüz
  bunları uydurmamalı.

## Product Principles

1. **Kanıt, iddianın önünde gelir.** Hiçbir bulgu kaynağı olmadan gösterilmez; skor tek
   başına karar gerekçesi sayılmaz.
2. **Karar insana aittir.** Sistem aday bulguları önerir, analist onaylar veya reddeder.
   Karar yeniden taramada korunur.
3. **Hukuki dayanak kaydın parçasıdır.** Vaka açılmadan dayanak ve saklama süresi
   belirlenir; bu alanlar sonradan arka planda doldurulamaz.
4. **Ne olacağı baştan bellidir.** Tarama öncesi süre, görev ve istek sayısı gösterilir;
   tahmin, sonuçtan ayrı tutulur.
5. **Kayıt, anlık görüntü değildir.** Veriler silinebilir, saklama süresi dolduğunda
   düşer, her kritik işlem denetim izine yazılır.

## Accessibility & Inclusion

- Hedef: WCAG 2.1 AA. Gövde metni 4.5:1, büyük metin 3:1 kontrast oranını karşılar.
- En küçük metin 12px'tir; yüksek DPI ekranlarda okunabilirlik için alt sınır bu.
- Klavye ile tam kullanılabilirlik: filtre sekmeleri ok tuşlarıyla gezilir, tüm
  etkileşimli öğeler görünür odak halkası taşır (`focus-visible`), odak sırası kayıt
  sırasını izler.
- Başlık hiyerarşisi atlamaz (h1 → h2 → h3).
- `prefers-reduced-motion` saygı görür: geçiş ve canlı göstergeler kapanır.
- Renk tek başına anlam taşımaz — durum her zaman nokta **ve** metin ile birlikte verilir.
