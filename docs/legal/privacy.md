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
