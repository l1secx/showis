/** CSV hücresi kaçışı: tırnak + Excel formül enjeksiyonu koruması. */
export function escapeCsvCell(value: unknown): string {
  const raw = String(value ?? "");
  // Excel/LibreOffice, = + - @ ve sekme/CR ile başlayan hücreleri formül sayar.
  const safe = /^\s*[=+\-@\t\r]/.test(raw) ? `'${raw}` : raw;
  return `"${safe.replace(/"/g, '""')}"`;
}
