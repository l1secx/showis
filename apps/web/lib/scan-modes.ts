/** Tarama modu tek kaynağı: yeni vaka formu, tarama paneli ve URL normalizasyonu. */
export const SCAN_MODES = [
  {
    value: "O1",
    label: "Hızlı",
    hint: "Arama ve API çağrıları, site kontrolü yok",
  },
  {
    value: "O2",
    label: "Standart",
    hint: "Arama, API ve 6 kullanıcı adı site kontrolü",
  },
  {
    value: "O3",
    label: "Derin",
    hint: "Standart mod ve tarayıcı ile 10 kullanıcı adı",
  },
] as const;

export type ScanMode = (typeof SCAN_MODES)[number]["value"];

const VALUES = new Set<string>(SCAN_MODES.map((mode) => mode.value));

/** URL/sessionStorage değerini API'nin kabul ettiği büyük harfli moda çevirir. */
export function normalizeScanMode(value: string | null | undefined): ScanMode | null {
  const text = (value ?? "").trim().toUpperCase();
  return VALUES.has(text) ? (text as ScanMode) : null;
}
