/** Ortak vaka etiketleri: hukuki dayanak ve durum adları. */
export const LEGAL_BASIS_LABELS: Record<string, string> = {
  consent: "Açık rıza",
  legitimate_interest: "Meşru menfaat",
  self_check: "Öz kontrol",
};

export function legalBasisLabel(value: string): string {
  return LEGAL_BASIS_LABELS[value] ?? value;
}
