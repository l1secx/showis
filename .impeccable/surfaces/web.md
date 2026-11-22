---
version: 1
slug: "web"
primary_target: "web"
related_targets: []
---

---
version: 1
slug: "web"
primary_target: "web"
related_targets: []
---

# Surface: Showis web uygulaması (tüm rotalar)

**Kapsam:** `/`, `/cases/new`, `/settings`, `/cases/[id]/*` — arayüzün tamamı.
**Ziyaretçi modu:** Operate. Ziyaretçi işi tamamlar: vaka açar, tarama çalıştırır, bulguları
inceler, karar verir, rapor üretir. Ziyaretçi başarısı, işi bitirmesi ve kararının
savunulabilir olmasıdır; ifade değil.

**Kitle ve iş:** Analist masaüstünde, gün içinde yoğun okuma yapar. Kayıtları karşılaştırır,
skor kırılımını okur, kanıt bağlantısını açar, karar verir. Kısıt: hukuki dayanak ve saklama
süresi kayda gömülüdür; yalnızca herkese açık kaynaklar taranır.

**Kısıtlar:** Türkçe arayüz; CSP `style-src 'self'` (uzak font yüklemesi engelli, yazı
tipleri sistem yığınından); API tokenı yalnızca sunucuda; HTML raporu sayfada render
edilmez; WCAG AA hedefi.

## Yön sözleşmesi

**THESIS:** Kayıt yüzeyi. Bu ürünün öznesi bir "platform" değil, bir **dosya**: açılmış bir
vaka, içinde hedef, tarama, bulgular, kanıt ve denetim izi. Arayüzün işi bu dosyayı
okunabilir ve savunulabilir kılmaktır. Bunun reddi: her sayfayı aynı kart ızgarasıyla
doldurmak, "hacker terminali" klişesine yaslanmak, durumları rozet çorbasına döndürmek.

**OWN-WORLD:** Soğuk kâğıt grisi zemin (#ECEEF1) üzerine beyaz yüzeyler (#FFFFFF), mürekkep
lacivert eylem aksanı (#1B3A5B), mürekkep siyahı (#15181D) metin. Ayraç ince ve tek; gölge
neredeyse yok, yalnızca yüzer yüzeylerde. Yuvarlaklık kontrollü: panel 8px, kontrol 6px.
Durum bir rozet değil, nokta + metin. Sayılar tabular; monospace yalnız kod, hash, kimlik
ve ölçüde.

**İki tema, tek dünya:** Karanlık tema ayrı bir kimlik değil, aynı dünyanın gece hâli.
Zemin derin grafit (#1A1D22), aksan tozlu mavi (#35618F) — parlak/neon değil. Renkler
ters çevrilmez, dünya ters çevrilir. Tema anahtarı başlık şeridinde; tercih localStorage'da,
ilk boyamadan önce uygulanır (flash yok).

**STORY:** Ziyaretçi kaydı açar, tarama ne kadar süreceğini önceden görür, tarama biter,
bulguları skor sırasıyla inceler, kırılımı açar, kanıtın kaynağına ve hash'ine bakar, karar
verir (onayla / reddet), raporu üretir. Her adım kayda geçer; denetim sekmesi bunu gösterir.
Analist gündüz açık temayı, gece vardiyasında koyu temayı kullanır; ikisi de aynı işi
kolaylaştırır.

**FIRST VIEWPORT:** `/` üzerinde: üstte 56px'lik beyaz başlık şeridi (solda nişangâh işareti +
"Showis" + ince ayraçlı "İnceleme kayıtları", ortada alt çizgili gezinme, sağda motor
durumu). Altında tek sütun, 1180px'e kadar daralır: sol hizalı "Vakalar" başlığı (26px) ve
tek satırlık açıklama; sağda "Toplam N kayıt" ölçüsü ve lacivert "Yeni vaka" düğmesi.
Bunun altında tek bir kontrol şeridi (arama + durum segmenti + sıralama), onun altında asıl
yüzey: **kayıt tablosu** — beyaz panel, ince ayraçlı satırlar, hizalı sayılar. İlk ekranın
kahramanı tablodur, süsleme değil.

**FORM:** Kayıt tablosu, sıralı listeden birinci sırada (puan 1). `seed: none (unavailable)`.
Form dili: tek yüzey + ince ayraç, kart ızgarası değil; durum nokta + metin; ölçü sayı +
3px ince çizgi; kayıt satırı panel içinde bölünmüş tek bir sicil.

**FINISH:** unreviewed and undocumented is unfinished; this build ends with the finish review,
the verdict, DESIGN.md, and every shipping raster carrying its provenance.
