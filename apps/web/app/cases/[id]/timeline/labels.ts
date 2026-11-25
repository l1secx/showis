/**
 * Zaman çizelgesi etiketleri — denetim aksiyonları ve durum değerleri.
 *
 * `vocabulary.ts` genel ekranların ortak sözlüğüdür ve bilinmeyen aksiyonu
 * ham adıyla gösterir. Zaman çizelgesi denetim kaydının tamamını çizdiği
 * için orada henüz karşılığı olmayan aksiyonlar burada Türkçeleştirilir.
 * Yeni bir backend aksiyonu eklenirse ham adıyla görünür (gizlenmez).
 */

import { auditActionLabel } from "../vocabulary";

const EXTRA_ACTION_LABELS: Record<string, string> = {
  // Havuz
  "pool.item.create": "Havuza kayıt eklendi",
  "pool.item.update": "Havuz kaydı güncellendi",
  "pool.item.delete": "Havuz kaydı silindi",
  "pool.item.merge": "Havuz kaydı birleştirildi",
  "pool.breach_check": "Sızıntı kontrolü çalıştırıldı",
  // AI araştırma
  "research.session.create": "AI araştırma başlatıldı",
  "research.session.completed": "AI araştırma tamamlandı",
  "research.session.cancel": "AI araştırma iptal edildi",
  "research.session.interrupt": "AI araştırma duraklatıldı",
  "research.session.resume": "AI araştırma sürdürüldü",
  "research.session.steer": "AI araştırmaya yön verildi",
  "research.session.follow_up": "AI araştırmaya ek soru soruldu",
  "research.session.answer": "AI araştırma sorusu yanıtlandı",
  "research.session.delete": "AI araştırma silindi",
  "research.avatars": "Avatar adayları çıkarıldı",
  "research.citations": "Atıflar çıkarıldı",
  // Muhakeme
  "reasoning.pivot": "Pivot çalıştırıldı",
  "reasoning.correlate": "Korelasyon çalıştırıldı",
  "reasoning.hypotheses.generate": "Hipotezler üretildi",
  "reasoning.hypothesis.create": "Hipotez eklendi",
  "reasoning.hypothesis.update": "Hipotez güncellendi",
  "reasoning.hypothesis.delete": "Hipotez silindi",
  "reasoning.relation.create": "İlişki eklendi",
  "reasoning.relation.update": "İlişki güncellendi",
  "reasoning.relation.delete": "İlişki silindi",
  // Bulgu
  "finding.screenshot": "Ekran görüntüsü kanıtı alındı",
  // İzleme
  "watch.run": "İzleme çalıştırıldı",
  "watch.alerts": "İzleme uyarıları üretildi",
  "monitor.update": "İzleme ayarı güncellendi",
  "monitor.scan": "İzleme taraması çalıştı",
  // Vaka
  "case.comment.create": "Yorum eklendi",
  "case.owner.update": "Vaka sahibi güncellendi",
  "case.webhook.update": "Webhook güncellendi",
  "case.export.stix": "STIX dışa aktarıldı",
  "case.export.misp": "MISP dışa aktarıldı",
  "case.export.maltego": "Maltego dışa aktarıldı",
  "case.report.create": "Rapor oluşturuldu",
  // Tedarik zinciri
  "supply_chain.node.create": "Tedarik zinciri düğümü eklendi",
  "supply_chain.node.update": "Tedarik zinciri düğümü güncellendi",
  "supply_chain.node.delete": "Tedarik zinciri düğümü silindi",
  "supply_chain.edge.create": "Tedarik zinciri ilişkisi eklendi",
  "supply_chain.edge.update": "Tedarik zinciri ilişkisi güncellendi",
  "supply_chain.edge.delete": "Tedarik zinciri ilişkisi silindi",
  // Sızıntı
  "breach.password_range": "Parola aralığı sorgulandı",
};

/** Denetim aksiyonu → Türkçe etiket; bilinmeyen aksiyon ham adıyla döner. */
export function timelineActionLabel(action: string): string {
  const key = action.trim();
  if (EXTRA_ACTION_LABELS[key]) return EXTRA_ACTION_LABELS[key];
  return auditActionLabel(key);
}

const STATUS_LABELS: Record<string, string> = {
  proposed: "öneri",
  candidate: "aday",
  confirmed: "onaylandı",
  rejected: "reddedildi",
  lead: "ipucu",
  queued: "sırada",
  running: "çalışıyor",
  completed: "tamamlandı",
  failed: "başarısız",
  cancelled: "iptal",
  pending: "bekliyor",
  done: "tamamlandı",
  active: "aktif",
};

/** Durum değeri → Türkçe; bilinmeyen değer ham kalır. */
export function timelineStatusLabel(status: string | null | undefined): string | null {
  if (!status) return null;
  return STATUS_LABELS[status] ?? status;
}
