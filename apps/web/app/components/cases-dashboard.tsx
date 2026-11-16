"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowRight, FolderSearch, Plus, Trash2 } from "lucide-react";
import { api, type CaseSummary } from "../../lib/api";

const LEGAL_LABELS: Record<string, string> = {
  consent: "Açık rıza",
  legitimate_interest: "Meşru menfaat",
  self_check: "Öz kontrol",
};

const STATUS_FILTERS = [
  { value: "", label: "Tümü" },
  { value: "active", label: "Aktif" },
  { value: "archived", label: "Arşiv" },
];

export function CasesDashboard() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const statusFilter = searchParams.get("status") || "";

  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    api
      .listCases()
      .then(setCases)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  const deleteCase = async (c: CaseSummary) => {
    if (!confirm(`"${c.name}" vakasını ve tüm verilerini silmek istediğinize emin misiniz?`)) return;
    try {
      await api.deleteCase(c.id);
      setCases((prev) => prev.filter((x) => x.id !== c.id));
    } catch (e: any) {
      setError(e.message);
    }
  };

  const filtered = statusFilter ? cases.filter((c) => c.status === statusFilter) : cases;

  const stats = useMemo(() => {
    const weekAgo = Date.now() - 7 * 86400 * 1000;
    return {
      total: cases.length,
      active: cases.filter((c) => c.status === "active").length,
      week: cases.filter((c) => new Date(c.created_at).getTime() >= weekAgo).length,
    };
  }, [cases]);

  const setFilter = (value: string) => {
    const params = new URLSearchParams(searchParams.toString());
    if (value) params.set("status", value);
    else params.delete("status");
    router.push(`/?${params.toString()}`);
  };

  return (
    <div className="space-y-6 animate-in">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="section-title">operasyon paneli</p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-100">Vakalar</h1>
          <p className="mt-1 max-w-xl text-sm text-slate-400">
            Kamuya açık kaynaklarda hesap tespiti, cross-platform ilişkilendirme ve kanıt zinciri.
          </p>
        </div>
        <Link href="/cases/new" className="btn-primary shrink-0">
          <Plus size={15} /> Yeni Vaka
        </Link>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {[
          { label: "Toplam vaka", value: stats.total, icon: FolderSearch },
          { label: "Aktif", value: stats.active, icon: ArrowRight },
          { label: "Son 7 gün", value: stats.week, icon: Plus },
        ].map((s, i) => {
          const Icon = s.icon;
          return (
            <div key={s.label} className={`card p-4 animate-in stagger-${i + 1}`}>
              <div className="flex items-center justify-between">
                <span className="text-[11px] uppercase tracking-wider text-slate-500">
                  {s.label}
                </span>
                <Icon size={14} className="text-slate-600" />
              </div>
              <p className="mono mt-2 text-2xl font-semibold text-slate-100">{s.value}</p>
            </div>
          );
        })}
      </div>

      <div className="flex items-center gap-2">
        {STATUS_FILTERS.map((f) => (
          <button
            key={f.value}
            onClick={() => setFilter(f.value)}
            className={`rounded-full border px-3 py-1 text-xs transition-colors ${
              statusFilter === f.value
                ? "border-blue-500/50 bg-blue-500/10 text-blue-200"
                : "border-slate-800 bg-slate-900 text-slate-400 hover:border-slate-700 hover:text-slate-200"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {error && (
        <div className="card border-red-500/40 bg-red-500/5 p-4 text-sm text-red-300">
          API&apos;ye ulaşılamadı: {error}. API servisinin çalıştığından emin olun.
        </div>
      )}

      {loading ? (
        <p className="text-sm text-slate-400">Yükleniyor…</p>
      ) : filtered.length === 0 ? (
        <div className="card p-12 text-center">
          <p className="text-sm text-slate-400">
            {statusFilter ? "Bu filtreye uyan vaka yok." : "Henüz vaka yok."}
          </p>
          <Link href="/cases/new" className="mt-3 inline-flex text-sm text-blue-400 hover:underline">
            İlk vakayı oluştur →
          </Link>
        </div>
      ) : (
        <div className="card overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-900/80 text-left">
              <tr className="text-[11px] uppercase tracking-wider text-slate-500">
                <th className="px-4 py-2.5 font-medium">Vaka</th>
                <th className="hidden px-4 py-2.5 font-medium md:table-cell">Amaç</th>
                <th className="px-4 py-2.5 font-medium">Dayanak</th>
                <th className="px-4 py-2.5 font-medium">Durum</th>
                <th className="px-4 py-2.5 font-medium">Oluşturma</th>
                <th className="w-10 px-4 py-2.5" />
              </tr>
            </thead>
            <tbody>
              {filtered.map((c) => (
                <tr
                  key={c.id}
                  className="group border-t border-slate-800/80 transition-colors hover:bg-slate-800/30"
                >
                  <td className="px-4 py-3">
                    <Link
                      href={`/cases/${c.id}/scan`}
                      className="font-medium text-slate-100 transition-colors hover:text-blue-300"
                    >
                      {c.name}
                    </Link>
                    <p className="mono text-[10px] text-slate-600">{c.id.slice(0, 8)}</p>
                  </td>
                  <td className="hidden max-w-xs truncate px-4 py-3 text-slate-400 md:table-cell">
                    {c.purpose}
                  </td>
                  <td className="px-4 py-3 text-slate-400">
                    {LEGAL_LABELS[c.legal_basis] || c.legal_basis}
                  </td>
                  <td className="px-4 py-3">
                    <span className="chip">{c.status === "active" ? "aktif" : c.status}</span>
                  </td>
                  <td className="mono px-4 py-3 text-[11px] text-slate-400">
                    {new Date(c.created_at).toLocaleString("tr-TR", {
                      dateStyle: "short",
                      timeStyle: "short",
                    })}
                  </td>
                  <td className="px-4 py-3">
                    <button
                      onClick={() => deleteCase(c)}
                      title="Vakayı sil"
                      className="rounded-md p-1.5 text-slate-600 opacity-0 transition-colors hover:bg-red-500/10 hover:text-red-300 group-hover:opacity-100"
                    >
                      <Trash2 size={14} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
