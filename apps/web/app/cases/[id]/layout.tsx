"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, usePathname, useRouter } from "next/navigation";
import {
  Download,
  FileSearch,
  GitBranch,
  Radar,
  ScrollText,
  Trash2,
  FileText,
} from "lucide-react";
import { api, type CaseDetail } from "../../../lib/api";
import { ScanSessionProvider } from "./scan-context";

const TABS = [
  { key: "scan", label: "Tarama", href: "scan", icon: Radar },
  { key: "findings", label: "Bulgular", href: "findings", icon: FileSearch },
  { key: "graph", label: "Grafik", href: "graph", icon: GitBranch },
  { key: "report", label: "Rapor", href: "report", icon: FileText },
  { key: "audit", label: "Denetim", href: "audit", icon: ScrollText },
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

  const load = useCallback(() => {
    api.getCase(id).then(setCaseData).catch((e) => setError(e.message));
  }, [id]);

  useEffect(load, [load]);

  const exportCase = async () => {
    const data = await api.exportCase(id);
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `showis-case-${id}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const deleteCase = async () => {
    if (!confirm("Bu vakayı ve tüm verilerini silmek istediğinize emin misiniz?")) return;
    await api.deleteCase(id);
    router.push("/");
  };

  if (error) {
    return (
      <div className="card border-red-500/40 bg-red-500/5 p-4 text-sm text-red-300">
        Vaka yüklenemedi: {error}
      </div>
    );
  }

  const base = `/cases/${id}`;
  const hints = caseData?.target?.hints || {};
  // §6.6: ad varsa "Ad Soyad", kullanıcı adı varsa "· @nick" (ikisi birlikte olabilir).
  const firstName = caseData?.target?.first_name?.trim() ?? "";
  const lastName = caseData?.target?.last_name?.trim() ?? "";
  const fullName = `${firstName} ${lastName}`.trim();
  const nick = (caseData?.target?.nickname ?? "").trim().replace(/^@/, "");
  const targetLabel = [fullName, nick ? `@${nick}` : ""].filter(Boolean).join(" · ");

  return (
    <ScanSessionProvider caseId={id}>
      <div className="space-y-6 animate-in">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-xs text-slate-500">
              <Link href="/" className="transition-colors hover:text-slate-300">
                Vakalar
              </Link>
              <span>/</span>
              <span className="mono truncate">{id.slice(0, 8)}</span>
            </div>
            <h1 className="mt-1 truncate text-2xl font-semibold tracking-tight text-slate-100">
              {caseData?.name ?? "Vaka"}
            </h1>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              {targetLabel && (
                <span className="chip border-blue-500/30 bg-blue-500/10 text-blue-200">
                  {targetLabel}
                </span>
              )}
              {hints.school && <span className="chip">🎓 {hints.school}</span>}
              {hints.city && <span className="chip">📍 {hints.city}</span>}
              {hints.company && <span className="chip">🏢 {hints.company}</span>}
              {caseData && (
                <span className="chip">
                  {LEGAL_LABELS[caseData.legal_basis] || caseData.legal_basis}
                </span>
              )}
              {caseData?.target?.review_required && (
                <span className="chip border-amber-500/40 bg-amber-500/10 text-amber-200">
                  inceleme gerekli
                </span>
              )}
            </div>
          </div>
          <div className="flex shrink-0 gap-2">
            <button onClick={exportCase} className="btn-ghost btn-sm">
              <Download size={13} /> JSON
            </button>
            <button onClick={deleteCase} className="btn-danger btn-sm">
              <Trash2 size={13} /> Sil
            </button>
          </div>
        </div>

        <div>
          <div className="flex gap-1 overflow-x-auto border-b border-slate-800">
            {TABS.map((t) => {
              const href = `${base}/${t.href}`;
              const active = pathname === href || pathname === `${href}/`;
              const Icon = t.icon;
              return (
                <Link
                  key={t.key}
                  href={href}
                  className={`-mb-px flex items-center gap-2 whitespace-nowrap border-b-2 px-4 py-2.5 text-sm transition-colors ${
                    active
                      ? "border-blue-400 font-medium text-slate-100"
                      : "border-transparent text-slate-400 hover:text-slate-200"
                  }`}
                >
                  <Icon size={14} className={active ? "text-blue-400" : ""} />
                  {t.label}
                </Link>
              );
            })}
          </div>

          <div className="pt-5">{children}</div>
        </div>
      </div>
    </ScanSessionProvider>
  );
}
