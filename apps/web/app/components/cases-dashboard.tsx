"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowDown, Loader2, Plus, Search, Trash2, X } from "lucide-react";
import { api, type CaseListItem, type CaseSort } from "../../lib/api";

const LEGAL_LABELS: Record<string, string> = {
  consent: "Açık rıza",
  legitimate_interest: "Meşru menfaat",
  self_check: "Öz kontrol",
};

const CASE_STATUS_LABELS: Record<string, string> = {
  active: "Aktif",
  review_pending: "İnceleme bekliyor",
  deleted: "Silinmiş",
  archived: "Arşivde",
};

/** Durum rengi yalnızca anlam taşır: nokta + metin. Rozet çorbası yok. */
const CASE_STATUS_TONE: Record<string, string> = {
  active: "bg-ok",
  review_pending: "bg-warn",
  deleted: "bg-danger",
  archived: "bg-ink-4",
};

const SCAN_STATUS_LABELS: Record<string, string> = {
  queued: "sırada",
  running: "çalışıyor",
  completed: "tamamlandı",
  failed: "başarısız",
  cancelled: "iptal",
};

const SCAN_STATUS_TONE: Record<string, string> = {
  queued: "bg-ink-4",
  running: "bg-accent",
  completed: "bg-ok",
  failed: "bg-danger",
  cancelled: "bg-ink-4",
};

const STATUS_FILTERS = [
  { value: "", label: "Tümü" },
  { value: "active", label: "Aktif" },
  { value: "review_pending", label: "İnceleme" },
];

const SORT_OPTIONS: { value: CaseSort; label: string }[] = [
  { value: "created_desc", label: "En yeni" },
  { value: "created_asc", label: "En eski" },
  { value: "name_asc", label: "Ad (A-Z)" },
  { value: "updated_desc", label: "Son tarama" },
  { value: "relevance", label: "İlgi düzeyi" },
];

const PAGE_SIZE = 50;

export function CasesDashboard() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const statusFilter = searchParams.get("status") || "";

  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [sort, setSort] = useState<CaseSort>("created_desc");
  const [items, setItems] = useState<CaseListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [grandTotal, setGrandTotal] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Arama kutusu: 300 ms debounce → sunucu (veya eski imajda istemci) filtresi.
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(query.trim()), 300);
    return () => clearTimeout(timer);
  }, [query]);

  // Arama başlarken ilgi sıralamasına geç; kullanıcı başka sıralama seçtiyse
  // dokunma. Arama temizlenince varsayılan "En yeni"ye dön.
  const hadQueryRef = useRef(false);
  useEffect(() => {
    const hadQuery = hadQueryRef.current;
    hadQueryRef.current = Boolean(debouncedQuery);
    if (debouncedQuery && !hadQuery) setSort("relevance");
    else if (!debouncedQuery && hadQuery) {
      setSort((current) => (current === "relevance" ? "created_desc" : current));
    }
  }, [debouncedQuery]);

  const refreshGrandTotal = useCallback(() => {
    api
      .listCases({ limit: 1 })
      .then((page) => setGrandTotal(page.total))
      .catch(() => setGrandTotal(null));
  }, []);

  useEffect(refreshGrandTotal, [refreshGrandTotal]);

  // Filtre/sıralama değişince ilk sayfayı yükle.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .listCases({
        q: debouncedQuery || undefined,
        status: statusFilter || undefined,
        sort,
        limit: PAGE_SIZE,
        offset: 0,
      })
      .then((page) => {
        if (cancelled) return;
        setItems(page.items);
        setTotal(page.total);
      })
      .catch((e: Error) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [debouncedQuery, statusFilter, sort]);

  const loadMore = async () => {
    setLoadingMore(true);
    setError(null);
    try {
      const page = await api.listCases({
        q: debouncedQuery || undefined,
        status: statusFilter || undefined,
        sort,
        limit: PAGE_SIZE,
        offset: items.length,
      });
      setItems((prev) => {
        const seen = new Set(prev.map((c) => c.id));
        return [...prev, ...page.items.filter((c) => !seen.has(c.id))];
      });
      setTotal(page.total);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoadingMore(false);
    }
  };

  const deleteCase = async (c: CaseListItem) => {
    if (!confirm(`"${c.name}" vakasını ve tüm verilerini silmek istediğinize emin misiniz?`))
      return;
    try {
      await api.deleteCase(c.id);
      setItems((prev) => prev.filter((x) => x.id !== c.id));
      setTotal((prev) => Math.max(0, prev - 1));
      refreshGrandTotal();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const setFilter = (value: string) => {
    const params = new URLSearchParams(searchParams.toString());
    if (value) params.set("status", value);
    else params.delete("status");
    const qs = params.toString();
    router.push(qs ? `/?${qs}` : "/");
  };

  const clearFilters = () => {
    setQuery("");
    const params = new URLSearchParams(searchParams.toString());
    params.delete("status");
    const qs = params.toString();
    router.push(qs ? `/?${qs}` : "/");
  };

  /**
   * Satırın tamamını tıklanabilir yapar. Firefox `<tr>` üzerinde
   * `position: relative` desteklemediği için kaplama (overlay) yerine
   * doğrudan yönlendirme kullanılır; iç bağlantı ve düğmeler hariç tutulur.
   */
  const openCase = (e: React.MouseEvent<HTMLElement>, id: string) => {
    if ((e.target as HTMLElement).closest("a, button, input, select, textarea, label")) return;
    router.push(`/cases/${id}/scan`);
  };

  const hasFilters = Boolean(query.trim() || statusFilter);
  const stats = useMemo(
    () => ({
      total: grandTotal ?? total,
      matched: total,
      shown: items.length,
    }),
    [grandTotal, total, items.length]
  );

  return (
    <div className="space-y-6">
      {/* Başlık: etiket yok, başlık kendi ağırlığını taşır. */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="page-title">Vakalar</h1>
          <p className="page-subtitle">
            Açık kaynak istihbarat kayıtları, kimlik profilleri ve çapraz platform
            doğrulamaları.
          </p>
        </div>
        <div className="flex items-center gap-5">
          <p className="text-[12px] text-ink-3">
            Toplam{" "}
            <span className="numeric ml-0.5 font-medium text-ink">{stats.total}</span> kayıt
          </p>
          <Link href="/cases/new" className="btn-primary shrink-0">
            <Plus size={15} aria-hidden />
            Yeni vaka
          </Link>
        </div>
      </div>

      {/* Kayıt üstü kontrol çubuğu */}
      <div className="panel">
        <div className="flex flex-col gap-3 p-3 lg:flex-row lg:items-center">
          <div className="relative flex-1">
            <Search
              size={15}
              aria-hidden
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-3"
            />
            <input
              type="search"
              role="searchbox"
              aria-label="Vaka ara"
              placeholder="Vaka adı, yasal amaç veya hedef kimliği ile arayın"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="input pl-9"
            />
            {query && (
              <button
                type="button"
                onClick={() => setQuery("")}
                aria-label="Aramayı temizle"
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-ink-3 transition-colors hover:text-ink"
              >
                <X size={13} aria-hidden />
              </button>
            )}
          </div>

          <div
            role="group"
            aria-label="Vaka durumu filtresi"
            className="flex items-center gap-0.5 rounded-[6px] border border-line-2 bg-sunken p-0.5"
          >
            {STATUS_FILTERS.map((f) => (
              <button
                key={f.value}
                type="button"
                aria-pressed={statusFilter === f.value}
                onClick={() => setFilter(f.value)}
                className={`rounded-[4px] px-2.5 py-1 text-[12px] transition-colors ${
                  statusFilter === f.value
                    ? "bg-surface font-medium text-ink shadow-scrim"
                    : "text-ink-3 hover:text-ink"
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>

          <label className="flex items-center gap-2 text-[12px] text-ink-3">
            Sırala
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value as CaseSort)}
              aria-label="Vaka sıralaması"
              className="select w-auto py-1 text-[12px]"
            >
              {SORT_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line px-3 py-2">
          <p className="numeric text-ink-3" aria-live="polite">
            {loading
              ? "yükleniyor"
              : hasFilters
                ? `${stats.matched} eşleşme / ${stats.total} kayıt`
                : `${stats.total} kayıt`}
          </p>
          {hasFilters && (
            <button type="button" onClick={clearFilters} className="btn-quiet btn-sm">
              <X size={12} aria-hidden />
              Filtreyi temizle
            </button>
          )}
        </div>
      </div>

      {error && (
        <div
          role="alert"
          className="rounded-[6px] border border-danger-line bg-danger-soft px-4 py-3 text-[13px] text-danger"
        >
          API&apos;ye ulaşılamadı: {error}. API servisinin çalıştığından emin olun.
        </div>
      )}

      {loading ? (
        <div className="panel flex items-center gap-2 px-5 py-8 text-[13px] text-ink-3">
          <Loader2 size={14} className="animate-spin" aria-hidden />
          Kayıtlar yükleniyor
        </div>
      ) : items.length === 0 ? (
        <div className="panel px-6 py-14 text-center">
          <p className="text-[13.5px] text-ink-2">
            {hasFilters
              ? "Aramanla eşleşen vaka yok. Filtreyi gevşetip yeniden deneyin."
              : "Henüz vaka kaydı yok. İlk incelemeyi başlatmak için hedefi tanımlayın."}
          </p>
          {hasFilters ? (
            <button type="button" onClick={clearFilters} className="btn-secondary btn-sm mt-4">
              <X size={12} aria-hidden />
              Filtreyi temizle
            </button>
          ) : (
            <Link href="/cases/new" className="btn-primary btn-sm mt-4">
              <Plus size={14} aria-hidden />
              Yeni vaka oluştur
            </Link>
          )}
        </div>
      ) : (
        <div className="space-y-3">
          {/* Telefonda tablo yatay kaydırma gerektirir ve durum görünmez.
              Dar ekranda aynı içerik kayıt listesi olarak dizilir. */}
          <ul className="panel divide-y divide-line sm:hidden">
            {items.map((c) => (
              <li
                key={c.id}
                onClick={(e) => openCase(e, c.id)}
                className="cursor-pointer px-4 py-3.5"
              >
                <div className="flex items-start justify-between gap-3">
                  <Link
                    href={`/cases/${c.id}/scan`}
                    className="min-w-0 flex-1 text-[13.5px] font-medium text-ink"
                  >
                    {c.name}
                  </Link>
                  <button
                    onClick={() => deleteCase(c)}
                    title="Kaydı sil"
                    aria-label={`${c.name} kaydını sil`}
                    className="-mr-1 shrink-0 rounded p-1 text-ink-3 transition-colors hover:bg-danger-soft hover:text-danger"
                  >
                    <Trash2 size={14} aria-hidden />
                  </button>
                </div>
                <p className="numeric mt-1 truncate text-ink-3">
                  {c.target?.nickname ? `@${c.target.nickname}` : null}
                  {c.target?.nickname && c.target?.first_name ? ", " : null}
                  {c.target?.first_name ? `${c.target.first_name} ${c.target.last_name ?? ""}` : null}
                  {!c.target?.nickname && !c.target?.first_name ? c.id.slice(0, 8) : null}
                </p>
                <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1.5">
                  <span className="status text-ink-2">
                    <span
                      className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                        CASE_STATUS_TONE[c.status] ?? "bg-ink-3"
                      }`}
                      aria-hidden
                    />
                    {CASE_STATUS_LABELS[c.status] || c.status}
                  </span>
                  <span className="text-[12px] text-ink-3">
                    {LEGAL_LABELS[c.legal_basis] || c.legal_basis}
                  </span>
                  {c.last_scan?.mode && (
                    <span className="numeric text-ink-3">{c.last_scan.mode}</span>
                  )}
                  <span className="numeric ml-auto text-ink-3">
                    {new Date(c.created_at).toLocaleDateString("tr-TR", {
                      day: "2-digit",
                      month: "2-digit",
                      year: "numeric",
                    })}
                  </span>
                </div>
              </li>
            ))}
          </ul>

          {/* Geniş ekranda sicil tablosu */}
          <div className="panel hidden overflow-hidden sm:block">
            <div className="scroll-x">
              <table className="data-table sm:min-w-[720px]">
                <thead>
                  <tr>
                    <th scope="col">Kayıt ve hedef</th>
                    <th scope="col" className="hidden md:table-cell">
                      Yasal dayanak
                    </th>
                    <th scope="col">Durum</th>
                    <th scope="col" className="hidden sm:table-cell">
                      Son tarama
                    </th>
                    <th scope="col">Oluşturma</th>
                    <th scope="col" className="w-10">
                      <span className="sr-only">İşlemler</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((c) => (
                    <tr
                      key={c.id}
                      onClick={(e) => openCase(e, c.id)}
                      className="cursor-pointer"
                    >
                      <td className="max-w-[22rem]">
                        <Link
                          href={`/cases/${c.id}/scan`}
                          className="block truncate text-[13.5px] font-medium text-ink transition-colors hover:text-accent-ink"
                        >
                          {c.name}
                        </Link>
                        <p className="numeric mt-0.5 truncate text-ink-3">
                          {c.target?.nickname ? `@${c.target.nickname}` : null}
                          {c.target?.nickname && c.target?.first_name ? ", " : null}
                          {c.target?.first_name ? `${c.target.first_name} ${c.target.last_name ?? ""}` : null}
                          {!c.target?.nickname && !c.target?.first_name ? c.id.slice(0, 8) : null}
                        </p>
                      </td>
                      <td className="hidden max-w-[14rem] truncate text-ink-2 md:table-cell">
                        {LEGAL_LABELS[c.legal_basis] || c.legal_basis}
                      </td>
                      <td>
                        <span className="status text-ink-2">
                          <span
                            className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                              CASE_STATUS_TONE[c.status] ?? "bg-ink-4"
                            }`}
                            aria-hidden
                          />
                          {CASE_STATUS_LABELS[c.status] || c.status}
                        </span>
                      </td>
                      <td className="hidden sm:table-cell">
                        {c.last_scan?.mode || c.last_scan?.status ? (
                          <span className="status text-ink-3">
                            <span
                              className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                                SCAN_STATUS_TONE[c.last_scan?.status ?? ""] ?? "bg-ink-4"
                              }`}
                              aria-hidden
                            />
                            <span className="numeric">{c.last_scan?.mode}</span>
                            <span className="text-ink-3">
                              {SCAN_STATUS_LABELS[c.last_scan?.status ?? ""] ||
                                c.last_scan?.status}
                            </span>
                          </span>
                        ) : (
                          <span className="text-ink-3">—</span>
                        )}
                      </td>
                      <td className="numeric whitespace-nowrap text-ink-3">
                        {new Date(c.created_at).toLocaleDateString("tr-TR", {
                          day: "2-digit",
                          month: "2-digit",
                          year: "numeric",
                        })}
                      </td>
                      <td className="text-right">
                        <button
                          onClick={() => deleteCase(c)}
                          title="Kaydı sil"
                          aria-label={`${c.name} kaydını sil`}
                          className="rounded p-1.5 text-ink-3 transition-colors hover:bg-danger-soft hover:text-danger"
                        >
                          <Trash2 size={14} aria-hidden />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {items.length < total && (
            <div className="flex items-center justify-between gap-3">
              <p className="numeric text-ink-3">
                {items.length} / {total} kayıt gösteriliyor
              </p>
              <button
                type="button"
                onClick={loadMore}
                disabled={loadingMore}
                className="btn-secondary btn-sm"
              >
                {loadingMore ? (
                  <Loader2 size={12} className="animate-spin" aria-hidden />
                ) : (
                  <ArrowDown size={12} aria-hidden />
                )}
                Daha fazla kayıt yükle
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
