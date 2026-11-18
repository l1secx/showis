# NOTICE — `wmn-sites.json` (türetilmiş veri)

Bu dizindeki `wmn-sites.json` dosyası **türetilmiş bir veri kümesidir**; Showis
kaynak kodu değildir. Kod MIT lisansı altında kalır, ancak bu JSON dosyasının
içeriği **CC BY-SA 4.0** koşullarına tabidir.

## Atıf (zorunlu)

| Alan | Değer |
|---|---|
| Eser | WhatsMyName (`wmn-data.json`) |
| Proje | <https://github.com/WebBreacher/WhatsMyName> |
| Telif | Copyright (C) 2015-2026 Micah Hoffman |
| Lisans | Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0) |
| Lisans metni | <https://creativecommons.org/licenses/by-sa/4.0/legalcode> · yerel kopya: `LICENSES/CC-BY-SA-4.0.txt` |
| Değişiklik | Evet — "adapted" (site kayıtları Showis `SiteSpec` şemasına dönüştürüldü) |

Katkıda bulunan WMN yazarları (kaynak dosyanın `authors` alanı): 3xp0rt,
AlexisPradoOficial, funnyzak, jschibberges, mbiesiad, Micah Hoffman, mtedholm,
NovaCode37, Owen Ross.

## Türetme nasıl yapıldı

Dönüşüm `scripts/import_wmn_sites.py` ile üretilir; elle düzenlenmez. Kayıtlar
birebir kopyalanmaz: alanlar Showis'in kullanıcı-adı kontrol motoruna
(`sitecheck`) uyarlanır.

| WMN alanı | Showis alanı | Not |
|---|---|---|
| `uri_check` (`{account}`) | `url_template` (`{u}`) | `http://` şablonları `https://`'e yükseltilir |
| `e_code` | `expect_status` | |
| `e_string` | `present_regex` | `re.escape` — WMN değeri düz metin (literal) alt dizedir |
| `m_string` | `absent_regex` | `re.escape` |
| `m_code` | `absent_statuses` | `m_code == e_code` ise boş bırakılır (kod ayırt edici değil) |
| `cat` | `category` | WMN kategorisi platform değil, sınıflandırmadır |
| `name` | `key` + `name` | `key` = slug; çakışmada `-2`, `-3` soneki |
| — | `platform` | Sabit `"web"` |
| — | `source` | Sabit `"wmn"` |

Dışlanan kayıtlar (gerekçe koddaki `_convert_record` içinde):

- `valid: false` → geçersiz ilan edilmiş kayıt,
- `post_body` dolu → motor yalnız GET atar,
- `protection` içinde `cloudflare` / `captcha` / `ddos-guard` / `anubis` → bot
  duvarı aşılamaz (bypass denenmez),
- ne `absent_statuses` ne `absent_regex` üretilebilen kayıt → "yok" kararı
  verilemez, yanlış pozitif üretmemek için dışlanır,
- normalize edilmiş `url_template` tekrarı → ilk kayıt tutulur.

## Bu veriyi kullanırken

- **Tespitler "lead"dir, kanıt değildir.** WMN verisi arşivlenmiş bir projeden
  gelir; siteler zamanla değişir (bot duvarı, alan adı ölümü, HTML değişimi).
  Sonuçlar skor kartında tek başına kimlik doğrulaması sayılmamalıdır.
- **WMN projesi 2026-11-09'da arşivlendi.** Depo kalıcı ve veri kullanılabilir,
  ancak upstream güncellemesi beklenmemelidir.
- **Paylaşım:** bu dosyayı değiştirip dağıtırsanız CC BY-SA 4.0 gereği aynı
  lisansla ve bu atıfı koruyarak dağıtmalısınız.
- Yeniden üretmek için:

  ```bash
  .venv/bin/python scripts/import_wmn_sites.py \
      --source /tmp/opencode/wmn-source.json \
      --out packages/connectors/showis_connectors/sitecheck/data/wmn-sites.json
  ```

  Kaynağı doğrudan indirmek için `--fetch` eklenir (ağ erişimi gerekir).
