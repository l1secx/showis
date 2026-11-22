/**
 * Backend'in makine adları → arayüz dili.
 *
 * API yanıtları alan/olay/tip adlarını ham biçimde taşır (`created_at`,
 * `cross_link`, `case.create`). Arayüz Türkçe olduğu için bu adlar burada
 * çevrilir. Bilinmeyen bir değer **gizlenmez**; ham adıyla gösterilir, böylece
 * yeni bir backend olayı arayüze eklenmeden önce de görünür.
 */

/** Kimlik/çapraz bağlantı kenar tipleri. */
export const EDGE_TYPE_LABELS: Record<string, string> = {
  same_person: "Aynı kişi",
  cross_link: "Çapraz bağlantı",
  has_account: "Hesap",
  mentions: "Bahsetme",
  owned_domain: "Alan adı",
};

export const edgeTypeLabel = (type: string): string =>
  EDGE_TYPE_LABELS[type] ?? type.replace(/_/g, " ");

/**
 * Kronoloji olayları — `report.py::_DATE_ATTRIBUTES` ile hizalı.
 * Hepsi bir hesabın oluşum zamanını gösterir; fark, kaynak alanın adıdır.
 */
export const TIMELINE_EVENT_LABELS: Record<string, string> = {
  created_at: "Hesap oluşturuldu",
  joined_at: "Hesaba katıldı",
  created: "Oluşturuldu",
  ctime: "Oluşturuldu",
};

export const timelineEventLabel = (event: string): string =>
  TIMELINE_EVENT_LABELS[event] ?? event.replace(/_/g, " ");

/**
 * Denetim kaydı aksiyonları — `apps/api` içindeki `record_audit` çağrılarıyla
 * hizalı (bilinmeyen bir aksiyon ham adıyla gösterilir).
 */
export const AUDIT_ACTION_LABELS: Record<string, string> = {
  "case.create": "Vaka oluşturuldu",
  "case.delete": "Vaka silindi",
  "case.export": "Vaka dışa aktarıldı",
  "scan.start": "Tarama başlatıldı",
  "scan.completed": "Tarama tamamlandı",
  "scan.cancelled": "Tarama iptal edildi",
  "scan.cancel": "Tarama iptal edildi",
  "scan.failed": "Tarama başarısız oldu",
  "finding.update": "Bulgu güncellendi",
  "report.create": "Rapor oluşturuldu",
  "report.download": "Rapor indirildi",
  "report.cleanup_failed": "Rapor temizliği başarısız oldu",
  "target.blocked": "Hedef engellendi",
  "settings.provider.update": "Sağlayıcı güncellendi",
  "settings.provider.delete": "Sağlayıcı silindi",
  "connector.task": "Bağlayıcı görevi çalıştı",
  "request.rejected": "İstek reddedildi",
  "auth.failed": "Kimlik doğrulama başarısız",
};

export const auditActionLabel = (action: string): string =>
  AUDIT_ACTION_LABELS[action] ?? action;
