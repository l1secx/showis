---
name: corporate-ui
description: Showis web arayüzü için kurumsal (corporate) tasarım sistemi. Koyu "AI slop" temalar, gradyanlar, neon aksanlar ve aşırı yuvarlak köşeler kullanılmaz; açık, resmi, SaaS/kurum sitesi estetiği uygulanır.
---

# Showis Kurumsal UI Kılavuzu

Bu skill `apps/web` (Next.js 15 + Tailwind 3) için geçerlidir. Yeni sayfa/bileşen
yazarken veya mevcut stilleri değiştirirken bu kurallara uy.

## Palet (açık tema — KESİNLİKLE koyu tema yok)

| Rol | Tailwind sınıfları |
|---|---|
| Sayfa zemini | `bg-slate-50` |
| Kart/panel | `bg-white border border-slate-200 rounded-lg shadow-sm` |
| Başlık metni | `text-slate-900 font-bold` / `font-semibold` |
| Gövde metni | `text-slate-700` / `text-slate-600` |
| Soluk metin | `text-slate-500` / `text-slate-400` |
| Ana aksan (buton, link, aktif sekme) | `blue-800` → hover `blue-700` |
| İkincil buton | `border border-slate-300 text-slate-700 hover:bg-slate-50` |
| Tehlike | `red-100 bg / red-800 text / border-red-300` |
| Uyarı | `amber-100 bg / amber-800 text / border-amber-300` |
| Başarı | `emerald-100 bg / emerald-800 text / border-emerald-300` |
| Tablo başlığı | `bg-slate-50 text-slate-600` + `border-b border-slate-200` satırları |
| Badge | `rounded-full text-xs px-2 py-0.5` + durum renkleri (STATUS_STYLE'a bak) |

## Kurallar

- **Yasak:** `bg-slate-9xx`, `text-slate-1xx/2xx/3xx` (açık zeminde okunmaz),
  `emerald-400/500/600` neon aksanlar, gradyanlar, `backdrop-blur`,
  `shadow-xl`, `rounded-2xl/3xl`, glow/glassmorphism.
- Köşe yarıçapı en fazla `rounded-lg`; `rounded-full` yalnızca badge/dot için.
- Gölge sadece `shadow-sm`; derin gölgeler yok.
- Birincil buton: `bg-blue-800 hover:bg-blue-700 text-white`.
- Aktif sekme: `border-b-2 border-blue-800 text-blue-800` (Settings'teki
  üst çizgi varyantı da kullanılabilir).
- Form input/select: `bg-white border border-slate-300 rounded-lg px-3 py-2
  text-slate-800 focus:border-blue-700 focus:outline-none`.
- Başlık hiyerarşisi: sayfa `text-2xl font-bold`, bölüm `text-lg font-semibold`,
  kart içi `font-semibold`. `text-3xl+` kullanma.
- Lucide ikonlar `lucide-react` paketinden; ince çizgi (stroke) ikonlar,
  dolgulu/emoji tarzı ikon yok.
- Türkçe UI metni; resmi ama sıcak dil. "→" yerine gerekirse `lucide` ok ikonu.
- Cytoscape grafik renkleri açık tema içindir: node etiketi `#334155`,
  edge `#cbd5e1`, edge etiketi `#64748b`; PLATFORM_COLORS marka renklerini korur.

## Mevcut sayfalar

- `app/page.tsx` — vaka listesi/tablosu
- `app/cases/new/page.tsx` — vaka oluşturma formu (bölümlü kartlar)
- `app/cases/[id]/page.tsx` — tarama/bulgu/grafik/rapor/denetim sekmeleri
- `app/settings/page.tsx` — BYOK sağlayıcı ayarları
- `app/layout.tsx` — header + footer (footer'da hukuki uyarı metni var)
