# Showis — Tasarım Sistemi (tek tema: siyah)

## Özet

Showis bir inceleme dosyası arayüzüdür. Zemin ve yüzey saf siyahtır (`0 0 0`).
Panelleri ton farkı değil **1px ince çizgi** ayırır. Tek tema vardır: koyu.
Tema anahtarı, `localStorage` tercihi ve `data-theme` dallanması yoktur.

- Düz: gölge yok. Yalnız gerçekten yüzen popover çok hafif gölge taşır.
- Köşe yarıçapı: panel `6px`, kontrol `4px`, çip `4px`.
- Tek eylem rengi: serin mavi/camgöbeği. Yeşil/amber/kırmızı yalnız anlam taşır.
- Sayılar tabular; monospace yalnız kod, hash, kimlik ve ölçüde.

## Tokenlar

Kaynak: `apps/web/app/globals.css`. Tailwind adları: `apps/web/tailwind.config.js`.
Renkler `R G B` kanalıdır; böylece `border-line/60` gibi opaklık değiştiricileri çalışır.

| Token | RGB | Rol |
|---|---|---|
| `--canvas` | `0 0 0` | sayfa zemini |
| `--surface` | `0 0 0` | panel, tablo, girdi |
| `--sunken` | `10 10 10` | gömülü yüzey, tablo başlığı |
| `--hover` | `14 14 14` | satır/düğme hover |
| `--overlay` | `12 12 12` | popover; 1px çizgiyle ayrışır |
| `--ink` | `250 250 250` | birincil metin |
| `--ink-2` | `200 200 200` | ikincil metin |
| `--ink-3` | `168 168 168` | üçüncül metin |
| `--ink-4` | `124 124 124` | yer tutucu, dekoratif nokta |
| `--line` | `33 33 33` | ana ayraç (beyaz ~%13) |
| `--line-2` | `46 46 46` | girdi/panel güçlü kenarı |
| `--accent` | `56 189 248` | düğme dolgusu, seçili sekme |
| `--accent-hover` | `14 165 233` | aksan hover |
| `--accent-soft` | `8 26 34` | seçili/ikincil dolgu |
| `--accent-line` | `22 62 82` | aksan çizgisi |
| `--accent-ink` | `125 211 252` | metin/bağlantı aksanı |
| `--on-accent` | `4 4 4` | aksan dolgu üstündeki metin |
| `--ok` | `74 222 128` | onay |
| `--ok-soft` | `8 22 14` | onay dolgusu |
| `--ok-line` | `26 74 46` | onay çizgisi |
| `--warn` | `250 204 21` | uyarı / aday |
| `--warn-soft` | `26 20 4` | uyarı dolgusu |
| `--warn-line` | `96 74 12` | uyarı çizgisi |
| `--danger` | `248 113 113` | hata / ret |
| `--danger-soft` | `30 12 12` | hata dolgusu |
| `--danger-line` | `100 36 36` | hata çizgisi |

## Kontrast (WCAG)

Siyah zemin üzerinde ölçüldü (`0 0 0`). En düşük değer, `sunken`/`hover` gibi
çok koyu yüzeylerde de AA'yı (≥4.5:1) korur.

| Metin | Oran (siyah) | En düşük (sunken/hover) | Sonuç |
|---|---|---|---|
| `ink` `#FAFAFA` | 20.12:1 | 18.50:1 | AAA |
| `ink-2` `#C8C8C8` | 12.55:1 | 11.54:1 | AAA |
| `ink-3` `#A8A8A8` | 8.83:1 | 8.12:1 | AAA |
| `ink-4` `#7C7C7C` | 5.03:1 | 4.62:1 | AA |
| `accent-ink` `#7DD3FC` | 12.60:1 | 11.58:1 | AAA |
| `ok` `#4ADE80` | 12.05:1 | 11.08:1 | AAA |
| `warn` `#FACC15` | 13.71:1 | 12.61:1 | AAA |
| `danger` `#F87171` | 7.59:1 | 6.98:1 | AAA |
| `on-accent` `#040404` (accent üstünde) | 9.57:1 | — | AAA |

Doğrulama betiği: WCAG bağıl parlaklık formülü (`sRGB → linear`, `(L1+0.05)/(L2+0.05)`).
Yer tutucu metin `ink-4`; gövde ve etiketler en az `ink-3` kullanır.

## Durum ve aksan kuralları

- Durum her zaman **nokta + metin**tir; renk tek başına anlam taşımaz.
- `-soft` dolgular siyah üzerinde çok koyu ve renkli kalır; gri panel görünümü vermez.
- Aksan dolgusu üstünde koyu metin (`on-accent`) kullanılır; aksan metni (`accent-ink`)
  siyah üzerinde yüksek kontrastlıdır.
- Odak: `2px solid accent` dış hat + `accent/25` halka. Klavye odağı her zaman görünür.
- Seçim: `accent/30` zemin, `ink` metin.
- Kaydırma çubuğu: 8px, koyu, `line-2` başparmak.

## Yerleşim ve bileşenler

- Kabuk: 56px yapışkan başlık (1px alt çizgi), `max-width: 1180px` içerik.
- Panel: siyah zemin + 1px `line`, `6px` yarıçap; gölge yok.
- Girdi/seçim: siyah zemin + 1px `line-2`, `4px` yarıçap.
- Düğmeler: `btn-primary` aksan dolgu; `btn-secondary` siyah + çizgi; `btn-quiet`
  çerçevesiz; `btn-danger` sakin kırmızı metin.
- Tablo: `thead` `sunken`, satırlar 1px `line`; hover `hover`.
- Popover: `overlay` zemin, 1px `line-2`, yalnız burada hafif gölge.
- Footer: tek satır — yasal özet + üç bağlantı; çok sütunlu footer yok.
- Gezinme: marka + üç sekme + motor durumu; tema düğmesi yok.

## Yapma

- Gri yüzey, kart ızgarası, katmanlı gölge veya ikinci tema ekleme.
- Neon parlaklık: aksan tek ve ölçülü kalır; durum renkleri yalnız anlam için.
- Tema için `localStorage`, `prefers-color-scheme` veya `data-theme` dallanması ekleme.
- 12px altı metin, kapsül rozet, dekoratif ok veya monospace kostümü kullanma.
