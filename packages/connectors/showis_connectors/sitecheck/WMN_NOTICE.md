# WhatsMyName alt kümesi — lisans bildirimi

`packages/connectors/showis_connectors/sitecheck/sites_wmn.py` içindeki site
tanımları **WhatsMyName** projesinden türetilmiştir:

- Kaynak: https://github.com/WebBreacher/WhatsMyName (`wmn-data.json`)
- Telif: Copyright (C) 2015-2026 Micah Hoffman ve WhatsMyName katkıda bulunanları
- Lisans: Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0)
  - https://creativecommons.org/licenses/by-sa/4.0/

Değişiklikler: `wmn-data.json` ham veri olarak saklanır; çalışma zamanında
yalnız anahtarsız, `e_string` + `m_string` çift işaretli, korumasız ve geçerli
kayıtlar `SiteSpec` biçimine dönüştürülür. `e_string` hesap-var, `m_string`
hesap-yok işareti olarak eşlenir. Mevcut Showis hostları tekrar eklenmez.
Veri setinin tamamı bellekte tutulur; O1/O2/O3 aktif kapsamı bütçe için
`sitecheck/sites.py::select_sites` içinde sınırlandırılır.

Bu dosya ve türetilmiş tanımlar CC BY-SA 4.0 koşulları altında paylaşılır.
Değişiklikler aynı lisans altında açık kaynak depoda yayımlanır.
