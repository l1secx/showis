import type { PoolItem } from "../../../../lib/api";

/**
 * Gürültü önayarı eşiği: bu güvenin altındaki onay bekleyen kayıtlar
 * "düşük kaliteli aday" sayılır. Filtre tamamen istemci tarafındadır;
 * varsayılan olarak gizler, tek anahtarla geri alınır.
 */
export const NOISE_CONFIDENCE_THRESHOLD = 0.45;

// Oturum açma ve 404 sayfaları: tarama çıktısı olarak bilgi taşımaz.
const NOISE_TITLE =
  /(?:\blog[ -]?in\b|\bsign[ -]?in\b|\bsignin\b|oturum\s*aç|giriş\s*yap|\b404\b|not\s*found|sayfa\s*bulunamadı)/i;

function isPending(item: PoolItem): boolean {
  return item.status === "proposed" || item.status === "candidate";
}

function looksLikeWebValue(item: PoolItem): boolean {
  return item.kind === "url" || /^https?:\/\//i.test(item.value.trim());
}

function normalizeValue(value: string): string {
  return value.trim().toLowerCase().replace(/\/+$/, "").replace(/\s+/g, " ");
}

/**
 * Her kayıt için gürültü sebeplerini döndürür. Yalnız onay bekleyen
 * (`proposed`/`candidate`) kayıtlar işaretlenir; kullanıcının onayladığı
 * veya reddettiği kayıtlar filtre tarafından gizlenmez.
 */
export function buildNoiseMap(items: PoolItem[]): Map<string, string[]> {
  const reasons = new Map<string, string[]>();
  const counts = new Map<string, number>();

  const add = (id: string, reason: string) => {
    const list = reasons.get(id) || [];
    if (!list.includes(reason)) list.push(reason);
    reasons.set(id, list);
  };

  // Tekrar, liste sırasından bağımsızdır: aynı tür+değer ikinci kez
  // görünüyorsa onay bekleyen kopya işaretlenir.
  for (const item of items) {
    const key = `${item.kind}::${normalizeValue(item.value)}`;
    counts.set(key, (counts.get(key) || 0) + 1);
  }

  for (const item of items) {
    if (!isPending(item)) continue;
    const key = `${item.kind}::${normalizeValue(item.value)}`;
    if (looksLikeWebValue(item) && NOISE_TITLE.test(`${item.label} ${item.value}`)) {
      add(item.id, "oturum açma/404 başlığı");
    }
    if (item.confidence < NOISE_CONFIDENCE_THRESHOLD) {
      add(item.id, `güven %${Math.round(Math.max(0, item.confidence) * 100)}`);
    }
    if ((counts.get(key) || 0) > 1) {
      add(item.id, "tekrar eden alan");
    }
  }

  return reasons;
}
