"use client";

/**
 * Tek tema: siyah. Bu modül geçmişte açık/koyu tema anahtarı taşıyordu;
 * API'si geriye dönük uyumluluk için korunur ama her zaman "dark" döner.
 * Sunucu ve istemci aynı değeri gördüğü için hydration farkı oluşmaz.
 */

export type Theme = "light" | "dark";

export const THEME: Theme = "dark";

export function readTheme(): Theme {
  return THEME;
}

export function useTheme(): Theme {
  return THEME;
}
