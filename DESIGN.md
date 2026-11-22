---
name: Showis
description: Kayıt yüzeyi — açık kaynak istihbarat inceleme kayıtları için kurumsal dosya arayüzü
colors:
  canvas: "#ECEEF1"
  surface: "#FFFFFF"
  sunken: "#F3F5F7"
  hover: "#F1F3F6"
  ink: "#15181D"
  ink-secondary: "#4A525E"
  ink-tertiary: "#616A76"
  line: "#D6DAE0"
  line-strong: "#B4BBC4"
  accent: "#1B3A5B"
  accent-hover: "#142C44"
  accent-soft: "#E9EFF5"
  accent-line: "#AFC5DB"
  ok: "#2A5B3F"
  ok-soft: "#E8F0EA"
  ok-line: "#B0CDBB"
  warn: "#855F14"
  warn-soft: "#F6F0E2"
  warn-line: "#DECDA7"
  danger: "#93342A"
  danger-soft: "#F7EBE9"
  danger-line: "#E0BEBA"
  canvas-dark: "#1A1D22"
  surface-dark: "#22262C"
  sunken-dark: "#1C2026"
  hover-dark: "#2A2F36"
  ink-dark: "#EDEFF2"
  ink-secondary-dark: "#B8C0CA"
  ink-tertiary-dark: "#8D96A2"
  line-dark: "#363C44"
  line-strong-dark: "#4E5660"
  accent-dark: "#35618F"
  accent-hover-dark: "#2C527A"
  accent-soft-dark: "#1F2E3F"
  accent-line-dark: "#3A5877"
  accent-ink-dark: "#84AAD1"
  ok-dark: "#6FBF8E"
  ok-soft-dark: "#1E2D24"
  ok-line-dark: "#3A5C46"
  warn-dark: "#D9A441"
  warn-soft-dark: "#2C2516"
  warn-line-dark: "#604E28"
  danger-dark: "#E28C80"
  danger-soft-dark: "#301D1A"
  danger-line-dark: "#643A30"
typography:
  display:
    fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif"
    fontSize: "26px"
    fontWeight: 600
    lineHeight: 1.15
    letterSpacing: "-0.022em"
  title:
    fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "-0.01em"
  body:
    fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif"
    fontSize: "13.5px"
    fontWeight: 400
    lineHeight: 1.55
  label:
    fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 500
    lineHeight: 1.45
  numeric:
    fontFamily: "JetBrains Mono, ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "12px"
    fontWeight: 400
    fontFeature: "tnum"
rounded:
  control: "6px"
  panel: "8px"
  chip: "4px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "32px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.surface}"
    rounded: "{rounded.control}"
    padding: "7px 12px"
  button-primary-hover:
    backgroundColor: "{colors.accent-hover}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink-secondary}"
    rounded: "{rounded.control}"
    padding: "7px 12px"
  button-quiet:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink-tertiary}"
    rounded: "{rounded.control}"
    padding: "5px 10px"
  button-danger-quiet:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.danger}"
    rounded: "{rounded.control}"
    padding: "5px 10px"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "7px 12px"
  panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "20px"
  status-ok:
    textColor: "{colors.ok}"
    rounded: "{rounded.chip}"
  status-warn:
    textColor: "{colors.warn}"
    rounded: "{rounded.chip}"
  status-danger:
    textColor: "{colors.danger}"
    rounded: "{rounded.chip}"
---

## Overview

**Creative North Star: "The Case File" (dosya masası).**

Showis bir "platform" değil, açılmış bir **dosya**: içinde hedef, tarama, bulgular, kanıt
zinciri ve denetim izi olan bir inceleme kaydı. Arayüzün bütün işi bu dosyayı okunabilir ve
savunulabilir kılmaktır — yani bir analist ekranı okuyup "bu hesap aynı kişi mi" sorusuna
kanıtla cevap verebiliyorsa tasarım işini yapmıştır.

Bu yüzden arayüz bir uygulama kabuğu gibi değil, bir **evrak masası** gibi kurulur: soğuk
kâğıt grisi bir zemin, üzerinde beyaz sayfalar, sayfaları birbirinden ayıran ince ve tek
çizgiler, ve mürekkep lacivert bir eylem rengi. Kullanıcı ekranı dolduran kutularla değil,
okunabilir kayıtlarla karşılaşır.

**Key Characteristics:**

- Düz ve tonlu: gölge neredeyse hiç yok, derinlik 1px çizgiyle ve yüzey tonuyla anlatılır.
- Sayılar öne çıkar: skor, sayaç ve tarih tabular rakamla yazılır; monospace yalnız kod,
  hash, kimlik ve ölçüde kullanılır.
- Durum bir rozet değil: nokta + metin. Renk tek başına anlam taşımaz.
- Boşluk yapı taşır: başlığın üstünde altından fazla boşluk; bölümler kutuyla değil
  ayraçla ayrılır.
- Tek eylem rengi: lacivert yalnız eylem içindir. Durum renkleri (yeşil/amber/kiremit)
  ayrı ve sınırlıdır.

## Colors

**Soğuk Kâğıt (canvas) — `#ECEEF1`.** Sayfanın zemini. Sıcak krem değil: hafif soğuk bir
gri, çünkü ürünün çıktısı kâğıt üzerine basılan bir rapor. Sayfa zemini, panellerin bir
ton açığıdır; paneller bu yüzden zeminle bir "kart ızgarası" gibi değil, zeminin üstündeki
belgeler gibi görünür.

**Mürekkep (ink) — `#15181D`, `#4A525E`, `#616A76`.** Üç metin tonu. Üçüncüsü
(`#616A76`) WCAG AA'yı geçen en açık metindir ve bu yüzden sistemde **dördüncü bir gri
metin tonu yoktur** — hiyerarşi ağırlık ve boyutla kurulur, renk koyuluğuyla değil.

**Mürekkep Lacivert (accent) — `#1B3A5B`.** Tek eylem rengi: birincil düğmeler, seçili
sekme, odak halkası, grafik kenarı. Neon değil; mürekkep gibi. Koyu lacivert arka plan
üzerinde değil, kâğıt üzerinde durduğu için kurumsal.

**Durum renkleri — ok `#2A5B3F`, uyarı `#855F14`, kırmızı `#93342A`.** Her biri açık bir
yüzey tonu, bir çizgi ve bir metin rengi olarak üçlü gelir (`-soft`, `-line`). Bir durum
her zaman metinle birlikte verilir; renk tek başına taşımaz.

**Platform renkleri** `apps/web/app/cases/[id]/platform-meta.ts` dosyasından gelir ve veri
kanalıdır: grafik düğümlerinde marka rengi **kenar** olarak, dolgu olarak değil (%16 opak
ton); platform etiketlerinde küçük bir kare. Koyu temada markanın koyu tonu görünmediği
için `accent` (açık ton) kullanılır — yani platform rengi de temayı izler.

## Themes

İki tema vardır ve **aynı dünyanın iki hâlidir**. Renkler ters çevrilmez; dünya ters
çevrilir. Koyu tema eski "koyu zemin + neon aksan" klişesine dönmez: zemin derin grafit
(`#1A1D22`), yüzeyler ondan bir ton açık, aksan tozlu mavi (`#35618F`) — parlak değil.

| | Açık | Koyu |
|---|---|---|
| Zemin (canvas) | `#ECEEF1` | `#1A1D22` |
| Yüzey (surface) | `#FFFFFF` | `#22262C` |
| Gömülü (sunken) | `#F3F5F7` | `#1C2026` |
| Mürekkep | `#15181D` | `#EDEFF2` |
| İkincil mürekkep | `#4A525E` | `#B8C0CA` |
| Üçüncül mürekkep | `#616A76` | `#8D96A2` |
| Ayraç | `#D6DAE0` | `#363C44` |
| Eylem aksanı | `#1B3A5B` | `#35618F` |
| Aksan (metin) | `#1B3A5B` | `#84AAD1` |
| Onay | `#2A5B3F` | `#6FBF8E` |
| Uyarı | `#855F14` | `#D9A441` |
| Kırmızı | `#93342A` | `#E28C80` |

**Mekanizma.** Tokenlar `app/globals.css` içinde iki blokta tanımlıdır: `:root` (açık) ve
`[data-theme="dark"]` (koyu). Sayfalar hiçbir zaman tema adına göre dallanmaz — daima
`bg-surface`, `text-ink-2` gibi anlamsal adları kullanır. Tek istisna, CSS'in
başlayabileceği birkaç yerde tema değiştiren betiklerdir (tema bootstrap'ı, cytoscape
stili) ve bunlar `data-theme` özniteliğini okur.

**Varsayılan ve kalıcılık.** Tercih `localStorage`'da `showis:theme` anahtarıyla saklanır.
Kayıt yoksa işletim sisteminin `prefers-color-scheme` tercihine uyulur. Tema, ilk boyamadan
önce `<head>` içindeki küçük bir betikle uygulanır — sayfa açık temayla açılıp koyuya
döndüğü bir "flash" oluşmaz.

**Tema anahtarı.** Başlık şeridinin sağında, 32px'lik bir simge düğmesi olarak durur.
Karanlık temada güneş, açık temada ay gösterir: düğme bir sonraki durumu değil, mevcut
durumu bildirir ve `aria-label` ile "Koyu temaya geç" / "Açık temaya geç" der.

**Gölge koyu temada derinleşir** (`--shadow-popover`), çünkü koyu zeminde gölge görünmez
hale gelir; yükselti orada çizgiyle anlatılır.

## Typography

Tek sans ailesi (Inter, sistem yığınına düşer) + tek monospace ailesi. İki yüz tipi yoktur;
başlık kendi ağırlığını taşır, ayrı bir display yüzü aranmaz. Yazı tipleri sistemden
gelir çünkü uygulamanın CSP'si `style-src 'self'` ile uzak font yüklemesini engeller.

- **26px / 600 / -0.022em** — sayfa başlığı (`page-title`). Tek sefer, sayfanın en üstünde.
- **15px / 600** — bölüm başlığı (`section-heading`).
- **13.5px / 1.55** — gövde. Analist aracı yoğunluğu.
- **12px** — etiket, açıklama, tablo başlığı, durum metni. **Alt sınır 12px'tir.**
- **12px monospace / tabular** — sayılar, hash'ler, kimlikler, tarihler.

Ölçü (satır uzunluğu) 66–72 karakter civarında tutulur (`max-w-[66ch]`); Türkçe metin
dar harflerden geniş olduğu için `ch` birimi ortalama karakter genişliğini yansıtmaz —
65ch pratikte ~72 karakter demektir.

**Monospace bir kostüm değildir.** Dekoratif etiketlerde, boşluk doldurmada veya "teknik"
gibi görünmek için kullanılmaz. Yalnız kod, hash, kimlik, tarih ve ölçüde kullanılır.

## Layout

- **Kabuk:** 56px'lik yapışkan beyaz başlık şeridi (1px alt çizgi), altında tek sütun,
  `max-width: 1180px`, 32px dikey ritim.
- **Sayfa ritmi:** sol hizalı her şey. Başlık bloğu → kontrol şeridi → asıl yüzey.
  Başlığın üstündeki boşluk altındakinden fazladır.
- **Kontroller:** tek bir şeritte — arama alanı esner, durum segmenti ve sıralama sabit.
- **Kayıt yüzeyi:** geniş ekranda tablo (beyaz panel, ince ayraçlı satırlar, `thead`
  gömülü gri), dar ekranda aynı içerik **kayıt listesi** olarak dizilir. Telefonda tablo
  yatay kaydırılmaz — durum görünmez kalırdı.
- **Vaka detayı:** başlık bloğu (başlık + alan/değer metadata satırı) → alt çizgili sekmeler
  → sekme içeriği. Metadata nokta ile birleştirilmiş tek bir dize değil, ayrı alanlardır.
- **Kaydırmalı tablolar:** `.scroll-x` sarmalayıcısı `contain: paint` kullanır; aksi halde
  Chrome'da tablonun taşan genişliği belge genişliğine sızar ve sayfa yatay kaydırılabilir
  hâle gelir.

## Elevation & Depth

**Düz bir sistem.** Yükselti bir kez ilan edilir: **1px çizgi**. Paneller, girdiler ve
tablo satırları gölgesizdir. Gölge yalnız gerçekten yüzen yüzeylerde vardır — popover
(`.shadow-popover`) ve açılır menü.

Sistemin "derinliği" renk tonundan gelir: zemin (#ECEEF1) → panel (#FFFFFF) → gömülü
yüzey (#F3F5F7). Üç ton, üç görev.

## Shapes

- **Panel:** 8px yuvarlaklık, 1px `line` çizgi, beyaz dolgu.
- **Kontrol (düğme, girdi, seçim):** 6px.
- **Segment ve rozet:** 4px. Kapsül (tam yuvarlak) kullanılmaz — durum bir nokta + metindir.
- **Çizgi kalınlığı:** 1px. Vurgu çizgisi (seçili sekme, odak) 2px.
- **Ölçü çubuğu:** 3px yükseklik, 1px yuvarlaklık — bir çizgi kadar ince, "dolgu" değil.

## Components

**Düğmeler.** `btn-primary` (lacivert dolgu, beyaz metin — sayfadaki tek birincil eylem),
`btn-secondary` (beyaz zemin, 1px çizgi), `btn-quiet` (çerçevesiz, yalnız metin),
`btn-danger-quiet` (tehlike metni kırmızı, arka plan kırmızı değil). Destructive eylem
başlıkta kırmızı dolguyla değil, sakin kırmızı metinle gösterilir.

**Girdiler.** 6px, 1px `line-strong` çizgi, beyaz zemin. Odak: `accent` çizgi + %15 opak
accent halka. Yer tutucu `ink-tertiary` (kontrast için `ink-4` gri tonu kullanılmaz).

**Durum.** Nokta (6px, durum rengi) + 12px metin. Rozet, dolu kutu, gradient yok.

**Kayıt tablosu.** `thead` gömülü (`sunken`), başlıklar 12px `ink-tertiary`; satırlar 1px
`line` ayraçlı; sayılar sağa hizalı ve tabular; satır hover'ı `hover` tonu.

**Bulgu kaydı.** Panel içinde bölünmüş tek bir sicil — her bulgu ayrı kart değil. Sol tarafta
etiket/platform/durum, açıklama, güvenilirlik ölçüsü; sağda büyük skor rakamı (19px) ve
onun altında 3px ölçü çubuğu; altında "Onayla / Reddet".

**Gezinme.** Başlık şeridinde alt çizgili sekmeler (aktif: 2px lacivert alt çizgi, lacivert
metin). Sekmeler ayrı kutular değil.

**Tema anahtarı.** Başlık şeridinin sağında 32px'lik simge düğmesi. Mevcut temayı gösterir
(karanlıkta güneş, açıkta ay), `aria-label` ile hangi temaya geçileceğini söyler. Düğme
çerçevesizdir; hover'da `hover` tonu alır, odak halkası görünürdür.

**Ölçü.** Sayı önce, çubuk yanında değil. Skor 19px tabular rakam; ölçü çubuğu onun
altında 3px ince çizgi.

**Durum bildirimi (banner).** 1px çizgi + `-soft` dolgu + durum metni. Satır içi uyarılar
(`-soft` zemin, çizgisiz) daha zayıf bilgi taşır. Eylem hatası asla yutulmaz: düğmeye
basıldığında ne olmadıysa kullanıcıya söylenir (teknik ayrıntı `title` üzerinde kalır).

**Makine adları çevrilir.** API yanıtları alan, olay ve tip adlarını ham taşır
(`case.create`, `created_at`, `cross_link`). Arayüz Türkçe olduğu için bu adlar
`app/cases/[id]/vocabulary.ts` sözlüğünde çevrilir: denetim aksiyonları, kronoloji olayları
ve kenar tipleri. Bilinmeyen bir değer **gizlenmez** — ham adıyla gösterilir, böylece
yeni bir backend olayı arayüze eklenmeden önce de görünür.

**Panel içinde ikinci kutu yoktur.** Panelin içine yerleştirilen her ayrı kutu ("güven
notu", "tahmin şeridi") bir yuvalanma hatasıdır: ayraç ve boşluk kullanılır. Panelin içi
tek yüzeydir; bölümler `border-t` ile ayrılır.

## Do's and Don'ts

### Do:

- **Do** yeni renk eklerken `app/globals.css` içindeki semantik tokenları kullan; sayfalarda
  `slate-500` gibi kademe adları yazma.
- **Do** durumu her zaman nokta **ve** metin ile birlikte göster; renk tek başına anlam
  taşımaz.
- **Do** sayıları tabular rakamla yaz; sütun hizası bozulmasın.
- **Do** gövde metnini `max-w-[66ch]` ile sınırla; ölçü 80 karakteri geçmesin.
- **Do** yeni bir sayfa kurarken önce başlığı, sonra tek bir kontrol şeridini, sonra asıl
  yüzeyi koy; kutuları sıralama.
- **Do** eylemi söyle: "Yakayı sil" değil, "Kaydı sil".
- **Do** odak halkasını görünür bırak; klavye kullanıcısı nerede olduğunu bilmelidir.

### Don't:

- **Don't** başlığın üstüne eyebrow etiketi (ALL-CAPS `section-title`) koyma — kaldırıldı,
  başlık kendi ağırlığını taşır.
- **Don't** durumları rozet çorbasına döndürme; her durum için ayrı bir dolu kutu yapma.
- **Don't** her bölümü kart içine koy; kart, içindeki başka bir şeyin kutusudur.
- **Don't** koyu zemin + neon aksan klişesine (near-black + single accent) dönme; koyu tema
  bile derin grafit ve tozlu maviyi kullanır — parlaklık ölçülür.
- **Don't** temayı sayfa bazında dallandırma; her yerde anlamsal token adları kullan.
- **Don't** gölgeyi dekorasyon olarak kullan; gölge yalnız yüzen yüzeyde.
- **Don't** monospace'ı "teknik görünmek" için kullan; yalnız kod, hash, kimlik, ölçü.
- **Don't** bağlantı metnine "→" ekle; ok dekoratif olmaktan çıkıp ekran okuyucu
  gürültüsüne dönüşür.
- **Don't** 12px'in altına inme; küçük metin yüksek DPI ekranlarda okunmaz.
- **Don't** bölüm başlıklarına 01 / 02 / 03 numarası koy; içerik gerçekten sıralı değilse
  numara bilgi taşımaz.
