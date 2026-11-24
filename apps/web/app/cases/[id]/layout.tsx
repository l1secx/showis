"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, usePathname, useRouter } from "next/navigation";
import { AlertTriangle, Download, Trash2, X } from "lucide-react";
import { api, type CaseDetail } from "../../../lib/api";

const TABS = [
  { key: "pool", label: "Havuz", href: "pool" },
  { key: "research", label: "AI Araştırma", href: "research" },
  { key: "supply-chain", label: "Tedarik Zinciri", href: "supply-chain" },
  { key: "scan", label: "Tarama", href: "scan" },
  { key: "findings", label: "Bulgular", href: "findings" },
  { key: "graph", label: "Grafik", href: "graph" },
  { key: "report", label: "Rapor", href: "report" },
  { key: "diff", label: "Değişim", href: "diff" },
  { key: "comments", label: "Yorumlar", href: "comments" },
  { key: "audit", label: "Denetim", href: "audit" },
];

const LEGAL_LABELS: Record<string, string> = {
  consent: "Açık rıza",
  legitimate_interest: "Meşru menfaat",
  self_check: "Öz kontrol",
};

export default function CaseLayout({ children }: { children: React.ReactNode }) {
  const { id } = useParams<{ id: string }>();
  const pathname = usePathname();
  const router = useRouter();
  const [caseData, setCaseData] = useState<CaseDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** Dışa aktarma / silme eylemi hatası — boşsa hata yok. */
  const [exportError, setExportError] = useState<{ message: string; detail?: string } | null>(
    null
  );

  const load = useCallback(() => {
    api.getCase(id).then(setCaseData).catch((e) => setError(e.message));
  }, [id]);

  useEffect(load, [load]);

  const exportCase = async () => {
    setExportError(null);
    try {
      const data = await api.exportCase(id);
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `showis-case-${id}.json`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e: any) {
      setExportError({
        message: "Kayıt dışa aktarılamadı. Bağlantıyı kontrol edip yeniden deneyin.",
        detail: e?.message,
      });
    }
  };

  const deleteCase = async () => {
    if (!confirm("Bu vakayı ve tüm verilerini silmek istediğinize emin misiniz?")) return;
    setExportError(null);
    try {
      await api.deleteCase(id);
      router.push("/");
    } catch (e: any) {
      setExportError({
        message: "Kayıt silinemedi. Bağlantıyı kontrol edip yeniden deneyin.",
        detail: e?.message,
      });
    }
  };

  if (error) {
    return (
      <div
        role="alert"
        className="rounded-[6px] border border-danger-line bg-danger-soft px-4 py-3 text-[13px] text-danger"
      >
        Vaka yüklenemedi: {error}
      </div>
    );
  }

  const base = `/cases/${id}`;
  const hints = caseData?.target?.hints || {};
  const firstName = caseData?.target?.first_name?.trim() ?? "";
  const lastName = caseData?.target?.last_name?.trim() ?? "";
  const fullName = `${firstName} ${lastName}`.trim();
  const nick = (caseData?.target?.nickname ?? "").trim().replace(/^@/, "");
  const hintValues = [
    hints.city ? `Şehir: ${hints.city}` : null,
    hints.company ? `Kurum: ${hints.company}` : null,
    hints.school ? `Okul: ${hints.school}` : null,
  ].filter(Boolean) as string[];

  // Kimlik alanları: etiket-değer çiftleri. Nokta ile birleştirilmiş meta
  // dizeleri yerine okunabilir alanlar.
  const targetKind = caseData?.target?.kind ?? "name";
  const fields: { label: string; value: React.ReactNode }[] = [
    fullName && targetKind === "name" ? { label: "Hedef", value: fullName } : null,
    nick && (targetKind === "name" || targetKind === "handle")
      ? { label: "Kullanıcı adı", value: `@${nick}` }
      : null,
    caseData?.target?.email ? { label: "E-posta", value: caseData.target.email } : null,
    caseData?.target?.phone ? { label: "Telefon", value: caseData.target.phone } : null,
    caseData?.target?.domain ? { label: "Alan adı", value: caseData.target.domain } : null,
    caseData?.target?.ip ? { label: "IP", value: caseData.target.ip } : null,
    caseData
      ? {
          label: "Hukuki dayanak",
          value: LEGAL_LABELS[caseData.legal_basis] || caseData.legal_basis,
        }
      : null,
    caseData?.owner ? { label: "Sahip", value: caseData.owner } : null,
    hintValues.length ? { label: "İpuçları", value: hintValues.join(", ") } : null,
    caseData
      ? { label: "Saklama süresi", value: `${caseData.retention_days} gün` }
      : null,
  ].filter(Boolean) as { label: string; value: React.ReactNode }[];

  return (
    <div className="space-y-6">
        {exportError && (
          <div
            role="alert"
            className="flex flex-wrap items-center justify-between gap-2 rounded-[6px] border border-danger-line bg-danger-soft px-3 py-2 text-[12.5px] text-danger"
          >
            <span className="inline-flex items-center gap-2" title={exportError.detail}>
              <AlertTriangle size={13} aria-hidden />
              {exportError.message}
            </span>
            <button
              type="button"
              onClick={() => setExportError(null)}
              className="btn-quiet btn-sm"
            >
              <X size={12} aria-hidden />
              Kapat
            </button>
          </div>
        )}
        {/* Kayıt başlığı */}
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0">
            <nav className="flex items-center gap-1.5 text-[12px] text-ink-3">
              <Link href="/" className="transition-colors hover:text-ink">
                Vakalar
              </Link>
              <span className="text-ink-3" aria-hidden>
                /
              </span>
              <span className="numeric text-ink-3">{id.slice(0, 8)}</span>
            </nav>
            <h1 className="page-title mt-1.5 truncate">
              {caseData?.name ?? "Vaka kaydı"}
            </h1>

            {fields.length > 0 && (
              <dl className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-1.5">
                {fields.map((f) => (
                  <div key={f.label} className="flex items-baseline gap-1.5">
                    <dt className="text-[12px] text-ink-3">{f.label}</dt>
                    <dd className="text-[12.5px] font-medium text-ink-2">{f.value}</dd>
                  </div>
                ))}
                {caseData?.target?.review_required && (
                  <div className="flex items-baseline gap-1.5">
                    <dt className="text-[12px] text-ink-3">Durum</dt>
                    <dd className="status-warn">İnceleme gerekli</dd>
                  </div>
                )}
              </dl>
            )}
          </div>

          <div className="flex shrink-0 items-center gap-2">
            <button onClick={exportCase} className="btn-secondary btn-sm">
              <Download size={13} aria-hidden />
              Dışa aktar
            </button>
            <button onClick={deleteCase} className="btn-danger-quiet btn-sm">
              <Trash2 size={13} aria-hidden />
              Kaydı sil
            </button>
          </div>
        </div>

        {/* Sekmeler */}
        <div>
          <div className="scroll-x flex gap-6 border-b border-line">
            {TABS.map((t) => {
              const href = `${base}/${t.href}`;
              const active = pathname === href || pathname === `${href}/`;
              return (
                <Link
                  key={t.key}
                  href={href}
                  prefetch={false}
                  aria-current={active ? "page" : undefined}
                  className={`relative flex items-center pb-2.5 pt-1 text-[13px] transition-colors ${
                    active
                      ? "font-medium text-ink after:absolute after:inset-x-0 after:bottom-[-1px] after:h-[2px] after:bg-accent"
                      : "text-ink-3 hover:text-ink"
                  }`}
                >
                  {t.label}
                </Link>
              );
            })}
          </div>

          <div className="pt-6">{children}</div>
        </div>
    </div>
  );
}
