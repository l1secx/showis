# Showis OSINT — Veri Saklama ve İmha Politikası (Retention Policy)

## 1. Standart Saklama Süreleri

| Veri Kategorisi | Saklama Süresi | Saklandığı Yer | İmha Yöntemi |
|---|---|---|---|
| **Ham Kanıt Snapshot'ları** (HTML, JSON, Ekran Görüntüsü) | **30 Gün** (Varsayılan) | MinIO (Object Storage) | Otomatik Purge (Celery Beat) & S3 Nesne Silme |
| **İşlenmiş Bulgular & Varlık Grafı** (Findings, Entities, Edges) | **90 Gün** | PostgreSQL Veritabanı | Soft delete ve periyodik temizlik |
| **Audit Kayıtları** (Audit Logs) | **365 Gün** | PostgreSQL Veritabanı | Arşivleme ve 1 yıl sonunda temizlik |

## 2. Vaka Bazlı İstisnalar
- Kullanıcı vaka oluştururken `retention_days` alanını özelleştirebilir (örn. 7 gün, 15 gün).
- Vakanın kullanıcı tarafından silinmesi (`DELETE /cases/{id}`) halinde ilgili tüm veriler süre beklenmeksizin anında kaskad silinir.
