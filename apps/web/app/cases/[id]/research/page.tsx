"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import {
  AlertTriangle,
  ChevronDown,
  ExternalLink,
  Loader2,
  Play,
  Sparkles,
  Square,
  Trash2,
} from "lucide-react";
import {
  api,
  type CaseListItem,
  type PoolItem,
  type ResearchMessage,
  type ResearchMeta,
  type ResearchSession,
} from "../../../../lib/api";

const STATUS_TR: Record<string, string> = {
  queued: "sırada",
  running: "çalışıyor",
  completed: "tamamlandı",
  failed: "başarısız",
  cancelled: "iptal",
};

const STATUS_DOT: Record<string, string> = {
  queued: "bg-ink-4",
  running: "bg-accent",
  completed: "bg-ok",
  failed: "bg-danger",
  cancelled: "bg-ink-4",
};

const STATUS_TEXT: Record<string, string> = {
  queued: "text-ink-3",
  running: "text-accent-ink",
  completed: "text-ok",
  failed: "text-danger",
  cancelled: "text-ink-3",
};

function Status({ value }: { value: string }) {
  return (
    <span className={`status ${STATUS_TEXT[value] || "text-ink-3"}`}>
      <span
        className={`h-1.5 w-1.5 shrink-0 rounded-full ${STATUS_DOT[value] || "bg-ink-4"}`}
        aria-hidden
      />
      {STATUS_TR[value] || value}
    </span>
  );
}

function ToolMessage({ message }: { message: ResearchMessage }) {
  const result = message.tool_result || {};
  const summary =
    result.error ||
    (Array.isArray(result.results) ? `${result.results.length} sonuç` : null) ||
    (Array.isArray(result.items) ? `${result.items.length} havuz kaydı` : null) ||
    result.name ||
    result.url ||
    result.scan_id ||
    "sonuç";
  return (
    <details className="rounded-[6px] border border-line bg-sunken/60 px-3 py-2 text-[12px] text-ink-3">
      <summary className="flex cursor-pointer list-none items-center gap-2 marker:content-none">
        <span className="numeric text-ink-3">{message.tool_name}</span>
        <span className="truncate text-ink-2">{String(summary).slice(0, 140)}</span>
        <ChevronDown size={12} aria-hidden className="ml-auto shrink-0" />
      </summary>
      {result.error ? (
        <p className="mt-2 text-danger">{String(result.error)}</p>
      ) : (
        <pre className="numeric mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-relaxed text-ink-2">
          {JSON.stringify(result, null, 2).slice(0, 6000)}
        </pre>
      )}
    </details>
  );
}

function ResearchPageInner() {
  const { id } = useParams<{ id: string }>();
  const params = useSearchParams();
  const router = useRouter();
  const [cases, setCases] = useState<CaseListItem[]>([]);
  const [meta, setMeta] = useState<ResearchMeta | null>(null);
  const [researchModel, setResearchModel] = useState("");
  const [sessions, setSessions] = useState<ResearchSession[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [session, setSession] = useState<ResearchSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [question, setQuestion] = useState("");
  const [mode, setMode] = useState<"research" | "agent">("research");
  const [depth, setDepth] = useState<"quick" | "standard" | "deep">("deep");
  const [bioItem, setBioItem] = useState<PoolItem | null>(null);
  const [followUp, setFollowUp] = useState("");
  const prefilled = useRef(false);
  const esRef = useRef<EventSource | null>(null);

  const activeStatus = session?.status;
  const isRunning = activeStatus === "queued" || activeStatus === "running";
  const anyRunning = sessions.some(
    (item) => item.status === "queued" || item.status === "running"
  );

  const loadSessions = useCallback(async () => {
    try {
      const data = await api.listResearch(id);
      setSessions(data.items);
      return data.items;
    } catch (e: any) {
      setError(e.message || "Araştırma oturumları yüklenemedi");
      return [];
    }
  }, [id]);

  const loadSession = useCallback(
    async (sessionId: string) => {
      try {
        const data = await api.getResearch(id, sessionId);
        setSession(data);
        return data;
      } catch (e: any) {
        setError(e.message || "Araştırma oturumu yüklenemedi");
        return null;
      }
    },
    [id]
  );

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.getResearchMeta(id), loadSessions(), api.listCases({ limit: 200 })])
      .then(([metaData, items, casePage]) => {
        if (cancelled) return;
        setMeta(metaData);
        setCases(casePage.items);
        setResearchModel(metaData.active_provider?.model || "");
        const requested = params.get("session");
        const initial = requested && items.some((s) => s.id === requested)
          ? requested
          : items[0]?.id || null;
        setSelectedId(initial);
        if (initial) void loadSession(initial);
      })
      .catch((e: any) => !cancelled && setError(e.message || "Araştırma yüklenemedi"))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  useEffect(() => {
    const q = params.get("q");
    if (q && !prefilled.current) {
      prefilled.current = true;
      setQuestion(q);
    }
  }, [params]);

  useEffect(() => {
    if (!selectedId) {
      setSession(null);
      return;
    }
    void loadSession(selectedId);
  }, [selectedId, loadSession]);

  // Canlı akış: SSE + yoklama yedeği.
  useEffect(() => {
    esRef.current?.close();
    esRef.current = null;
    if (!selectedId || !isRunning) return;
    const es = new EventSource(`/api/v1/cases/${id}/research/${selectedId}/events`);
    esRef.current = es;
    const refresh = () => {
      void loadSession(selectedId);
      void loadSessions();
    };
    [
      "research.status",
      "research.activity",
      "research.message",
      "research.completed",
      "research.failed",
      "research.cancelled",
    ].forEach(
      (name) => es.addEventListener(name, refresh as EventListener)
    );
    es.onerror = () => es.close();
    const poll = setInterval(refresh, 2500);
    return () => {
      es.close();
      clearInterval(poll);
      if (esRef.current === es) esRef.current = null;
    };
  }, [id, selectedId, isRunning, loadSession, loadSessions]);

  useEffect(() => {
    if (!session || session.status !== "completed") {
      setBioItem(null);
      return;
    }
    let cancelled = false;
    api
      .getPool(id, { kind: "bio", limit: 1 })
      .then((data) => {
        if (!cancelled) setBioItem(data.items[0] || null);
      })
      .catch(() => {
        if (!cancelled) setBioItem(null);
      });
    return () => {
      cancelled = true;
    };
  }, [id, session?.id, session?.status]);

  const start = async (e: React.FormEvent) => {
    e.preventDefault();
    if (question.trim().length < 10) {
      setError("Araştırma sorusu en az 10 karakter olmalı.");
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const created = await api.createResearch(id, {
        question: question.trim(),
        mode,
        depth,
        model: researchModel || undefined,
      });
      setSelectedId(created.id);
      setSession(created);
      setQuestion("");
      await loadSessions();
    } catch (err: any) {
      setError(err.message || "Araştırma başlatılamadı");
    } finally {
      setBusy(false);
    }
  };

  const sendFollowUp = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!session || followUp.trim().length < 2) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await api.addResearchMessage(id, session.id, followUp.trim());
      setFollowUp("");
      await loadSession(session.id);
      await loadSessions();
    } catch (err: any) {
      setError(err.message || "Takip sorusu gönderilemedi");
    } finally {
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (!session) return;
    setBusy(true);
    try {
      await api.cancelResearch(id, session.id);
      await loadSession(session.id);
      await loadSessions();
    } catch (e: any) {
      setError(e.message || "Araştırma iptal edilemedi");
    } finally {
      setBusy(false);
    }
  };

  const saveFinalToPool = async (answer: string, citations: ResearchMessage["citations"]) => {
    setBusy(true);
    setError(null);
    try {
      const sources = (citations || [])
        .map((citation) => citation.url || citation.label || "")
        .filter(Boolean);
      const value =
        answer + (sources.length ? `\n\nKaynaklar:\n- ${sources.join("\n- ")}` : "");
      await api.addPoolItem(id, {
        kind: "note",
        label: "AI nihai sentezi",
        value: value.slice(0, 20000),
        provenance: { research_session_id: session?.id },
      });
      setError(null);
      setNotice("Nihai sentez havuza not olarak eklendi.");
    } catch (e: any) {
      setError(e.message || "Sentez havuza eklenemedi");
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!session || !confirm("Bu araştırma oturumu silinsin mi?")) return;
    setBusy(true);
    try {
      await api.deleteResearch(id, session.id);
      setSelectedId(null);
      setSession(null);
      await loadSessions();
    } catch (e: any) {
      setError(e.message || "Oturum silinemedi");
    } finally {
      setBusy(false);
    }
  };

  const finalMessageId = session?.stats?.finished_message_id;
  const messages = session?.messages || [];
  const provider = meta?.active_provider;

  const sessionOptions = useMemo(
    () =>
      sessions.map((s) => ({
        ...s,
        createdLabel: s.created_at
          ? new Date(s.created_at).toLocaleString("tr-TR", {
              day: "2-digit",
              month: "2-digit",
              hour: "2-digit",
              minute: "2-digit",
            })
          : "",
      })),
    [sessions]
  );

  return (
    <div className="space-y-6">
      <div>
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="section-heading">AI araştırma</h2>
          <label className="flex items-center gap-1.5 text-[12px] text-ink-3">
            Vaka
            <select
              className="select w-auto max-w-[280px]"
              value={id}
              onChange={(e) => router.push(`/cases/${e.target.value}/research`)}
            >
              {!cases.some((item) => item.id === id) && <option value={id}>Mevcut vaka</option>}
              {cases.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        <p className="page-subtitle">
          Havuzdaki kanıtları ve kamuya açık webi kullanarak sorunuzu araştırır; her
          iddiayı kaynakla eşler ve nihai sentezi sunar. AI kayıtları siz onaylamadan
          onaylı sayılmaz. Sağlayıcıya gönderilen metin redaksiyondan geçer; yine de
          gereksiz hassas kişisel veri yüklemeyin.
        </p>
      </div>

      {error && (
        <div
          role="alert"
          className="rounded-[6px] border border-danger-line bg-danger-soft px-3 py-2 text-[12.5px] text-danger"
        >
          {error}
        </div>
      )}
      {notice && (
        <div
          role="status"
          className="rounded-[6px] border border-ok-line bg-ok-soft px-3 py-2 text-[12.5px] text-ok"
        >
          {notice}
        </div>
      )}

      {!loading && !provider && (
        <div className="rounded-[6px] border border-warn-line bg-warn-soft px-4 py-3 text-[12.5px] leading-relaxed text-warn">
          <span className="inline-flex items-center gap-2 font-medium">
            <AlertTriangle size={13} aria-hidden />
            Aktif AI sağlayıcısı yok
          </span>
          <p className="mt-1">
            Ayarlar ekranından OpenCode Go veya OpenAI uyumlu bir profil ekleyip aktifleştirin.
            Profil eklenene kadar araştırma başlatılamaz.
          </p>
          <Link href="/settings" className="mt-2 inline-block font-medium underline">
            Sağlayıcı ayarlarına git
          </Link>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="space-y-6">
          <section className="panel">
            <div className="panel-head">
              <div>
                <h2 className="panel-title">Yeni araştırma</h2>
                <p className="mt-0.5 text-[12px] text-ink-3">
                  {provider
                    ? `Aktif sağlayıcı: ${provider.name} · ${provider.model || "model belirtilmemiş"}`
                    : "Önce bir AI sağlayıcısı yapılandırın."}
                </p>
              </div>
            </div>
            <form onSubmit={start} className="space-y-4 p-5">
              <label className="block">
                <span className="label">Araştırma sorusu</span>
                <textarea
                  className="input min-h-[92px] resize-y"
                  placeholder="Örn: X şirketinin tedarik zincirindeki tedarikçileri ve ilişkileri kamuya açık kaynaklardan araştır."
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  maxLength={4000}
                  required
                />
              </label>

              {provider?.models && provider.models.length > 1 && (
                <label className="block">
                  <span className="label">Bu araştırma için model</span>
                  <select
                    className="select"
                    value={researchModel || provider.models[0]}
                    onChange={(e) => setResearchModel(e.target.value)}
                  >
                    {provider.models.map((model) => (
                      <option key={model} value={model}>
                        {model}
                      </option>
                    ))}
                  </select>
                </label>
              )}

              <fieldset>
                <legend className="label">Araştırma derinliği</legend>
                <div className="mt-1 grid gap-2 sm:grid-cols-3">
                  {[
                    {
                      value: "quick" as const,
                      title: "Hızlı",
                      desc: "~5 dk · 14 arama · 1 dalga",
                    },
                    {
                      value: "standard" as const,
                      title: "Standart",
                      desc: "~15 dk · 30 arama · 2 dalga",
                    },
                    {
                      value: "deep" as const,
                      title: "Derin",
                      desc: "~30 dk · 60 arama · 3 dalga · 4 alt ajan",
                    },
                  ].map((option) => (
                    <label
                      key={option.value}
                      className={`flex cursor-pointer items-start gap-2.5 rounded-[6px] border px-3 py-2.5 transition-colors ${
                        depth === option.value
                          ? "border-accent bg-accent-soft"
                          : "border-line-2 bg-surface hover:border-ink-4"
                      }`}
                    >
                      <input
                        type="radio"
                        name="research-depth"
                        value={option.value}
                        checked={depth === option.value}
                        onChange={() => setDepth(option.value)}
                        className="mt-[3px] h-3.5 w-3.5 shrink-0 accent-[rgb(var(--accent))]"
                      />
                      <span>
                        <span className="block text-[13px] font-medium text-ink">
                          {option.title}
                        </span>
                        <span className="mt-0.5 block text-[12px] leading-snug text-ink-3">
                          {option.desc}
                        </span>
                      </span>
                    </label>
                  ))}
                </div>
              </fieldset>

              <fieldset>
                <legend className="label">Yetki modu</legend>
                <div className="mt-1 grid gap-2 sm:grid-cols-2">
                  {[
                    {
                      value: "research" as const,
                      title: "Araştır ve öner",
                      desc: "Web'de arar, havuzu tarar; havuza ve tedarik zincirine ekledikleri onay bekler.",
                    },
                    {
                      value: "agent" as const,
                      title: "Tam ajan",
                      desc: "Aday kayıtları doğrudan yazar ve OSINT taraması başlatabilir.",
                    },
                  ].map((option) => (
                    <label
                      key={option.value}
                      className={`flex cursor-pointer items-start gap-2.5 rounded-[6px] border px-3 py-2.5 transition-colors ${
                        mode === option.value
                          ? "border-accent bg-accent-soft"
                          : "border-line-2 bg-surface hover:border-ink-4"
                      }`}
                    >
                      <input
                        type="radio"
                        name="research-mode"
                        value={option.value}
                        checked={mode === option.value}
                        onChange={() => setMode(option.value)}
                        className="mt-[3px] h-3.5 w-3.5 shrink-0 accent-[rgb(var(--accent))]"
                      />
                      <span>
                        <span className="block text-[13px] font-medium text-ink">
                          {option.title}
                        </span>
                        <span className="mt-0.5 block text-[12px] leading-snug text-ink-3">
                          {option.desc}
                        </span>
                      </span>
                    </label>
                  ))}
                </div>
              </fieldset>

              <button
                type="submit"
                disabled={busy || !provider || anyRunning || question.trim().length < 10}
                className="btn-primary btn-sm"
              >
                {busy && !anyRunning ? (
                  <Loader2 size={13} className="animate-spin" aria-hidden />
                ) : (
                  <Play size={13} aria-hidden />
                )}
                Araştırmayı başlat
              </button>
              {anyRunning && (
                <p className="text-[12px] text-ink-3">
                  Bu vakada çalışan bir araştırma var; bitmeden yenisini başlatamazsınız.
                </p>
              )}
            </form>
          </section>

          {session && (
            <section className="panel">
              <div className="panel-head flex-wrap">
                <div className="min-w-0">
                  <h2 className="panel-title truncate">{session.title}</h2>
                  <div className="mt-1 flex flex-wrap items-center gap-3 text-[12px] text-ink-3">
                    <Status value={session.status} />
                    <span>{session.mode === "agent" ? "Tam ajan" : "Araştır ve öner"}</span>
                    <span>
                      {session.stats?.depth === "quick"
                        ? "Hızlı"
                        : session.stats?.depth === "standard"
                          ? "Standart"
                          : "Derin"}
                    </span>
                    {typeof session.stats?.waves === "number" && (
                      <span>{session.stats.waves} dalga</span>
                    )}
                    {typeof session.stats?.subagents === "number" && (
                      <span>{session.stats.subagents} alt ajan</span>
                    )}
                    {typeof session.stats?.queries === "number" && (
                      <span>{session.stats.queries} arama</span>
                    )}
                    {typeof session.stats?.pages === "number" && (
                      <span>{session.stats.pages} sayfa</span>
                    )}
                    {typeof session.stats?.findings === "number" && (
                      <span>{session.stats.findings} bulgu</span>
                    )}
                    {typeof session.stats?.leads === "number" && (
                      <span>{session.stats.leads} ipucu</span>
                    )}
                    {typeof session.stats?.writes === "number" && (
                      <span>{session.stats.writes} yazma</span>
                    )}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  {isRunning && (
                    <button
                      type="button"
                      onClick={cancel}
                      disabled={busy}
                      className="btn-danger-quiet btn-sm"
                    >
                      <Square size={12} aria-hidden />
                      İptal
                    </button>
                  )}
                  {!isRunning && (
                    <button
                      type="button"
                      onClick={remove}
                      disabled={busy}
                      className="btn-danger-quiet btn-sm"
                    >
                      <Trash2 size={12} aria-hidden />
                      Sil
                    </button>
                  )}
                </div>
              </div>

              {session.error && (
                <p className="border-t border-line px-5 py-3 text-[12.5px] text-danger">
                  {session.error}
                </p>
              )}

              {isRunning && (
                <div className="flex items-center gap-2 border-t border-line bg-accent-soft/40 px-5 py-2.5 text-[12.5px] text-accent-ink">
                  <Loader2 size={13} className="animate-spin" aria-hidden />
                  <span className="font-medium">
                    {session.activity || session.stats?.activity || "AI çalışıyor"}
                  </span>
                </div>
              )}

              {bioItem && (
                <div className="border-t border-line bg-accent-soft/30 px-5 py-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <Sparkles size={13} className="text-accent-ink" aria-hidden />
                    <span className="text-[12px] font-medium text-accent-ink">
                      AI biyografisi
                    </span>
                    <Link
                      href={`/cases/${id}/pool?kind=bio`}
                      className="ml-auto text-[12px] font-medium underline"
                    >
                      Havuzda aç
                    </Link>
                  </div>
                  <div className="mt-2 max-h-[320px] overflow-auto whitespace-pre-wrap break-words text-[12.5px] leading-relaxed text-ink-2">
                    {bioItem.value}
                  </div>
                </div>
              )}

              <div className="space-y-3 border-t border-line p-5">
                {messages.length === 0 && (
                  <p className="text-[12.5px] text-ink-3">Henüz adım yok.</p>
                )}
                {messages.map((message) => {
                  if (message.role === "tool") return <ToolMessage key={message.id} message={message} />;
                  if (message.role === "user") {
                    return (
                      <div
                        key={message.id}
                        className="rounded-[6px] border-l-2 border-accent bg-accent-soft/50 px-3 py-2 text-[13px] leading-relaxed text-ink"
                      >
                        <p className="text-[11px] font-medium text-accent-ink">Kullanıcı</p>
                        <p className="mt-1 whitespace-pre-wrap">{message.content}</p>
                      </div>
                    );
                  }
                  const isFinal = finalMessageId === message.id;
                  return (
                    <div
                      key={message.id}
                      className={
                        isFinal
                          ? "rounded-[6px] border border-line bg-surface px-4 py-3"
                          : "px-1 text-[12.5px] leading-relaxed text-ink-3"
                      }
                    >
                      {isFinal ? (
                        <>
                          <div className="flex items-center gap-2 text-[11px] font-medium text-ok">
                            <Sparkles size={12} aria-hidden />
                            Nihai sentez
                          </div>
                          <div className="mt-2 whitespace-pre-wrap text-[13px] leading-relaxed text-ink">
                            {message.content}
                          </div>
                          {message.citations.length > 0 && (
                            <div className="mt-3 border-t border-line pt-2">
                              <p className="text-[11px] font-medium text-ink-3">Kaynaklar</p>
                              <ul className="mt-1 space-y-1">
                                {message.citations.map((citation, index) => (
                                  <li key={index} className="text-[12px] text-ink-2">
                                    {citation.url ? (
                                      <a
                                        href={citation.url}
                                        target="_blank"
                                        rel="noreferrer noopener"
                                        className="inline-flex items-center gap-1 text-accent-ink hover:underline"
                                      >
                                        {citation.label || citation.url}
                                        <ExternalLink size={11} aria-hidden />
                                      </a>
                                    ) : citation.pool_item_id ? (
                                      <Link
                                        href={`/cases/${id}/pool`}
                                        className="text-accent-ink hover:underline"
                                      >
                                        {citation.label || `Havuz kaydı ${citation.pool_item_id}`}
                                      </Link>
                                    ) : citation.node_id ? (
                                      <Link
                                        href={`/cases/${id}/supply-chain`}
                                        className="text-accent-ink hover:underline"
                                      >
                                        {citation.label || `Tedarik zinciri düğümü ${citation.node_id}`}
                                      </Link>
                                    ) : (
                                      citation.label
                                    )}
                                  </li>
                                ))}
                              </ul>
                            </div>
                          )}
                          <button
                            type="button"
                            onClick={() => saveFinalToPool(message.content, message.citations)}
                            disabled={busy}
                            className="btn-secondary btn-sm mt-3"
                          >
                            Nihai sentezi havuza ekle
                          </button>
                        </>
                      ) : (
                        <p className="whitespace-pre-wrap">{message.content}</p>
                      )}
                    </div>
                  );
                })}
              </div>

              {!isRunning && (
                <form onSubmit={sendFollowUp} className="border-t border-line p-5">
                  <label className="block">
                    <span className="label">Takip sorusu</span>
                    <div className="flex gap-2">
                      <input
                        className="input"
                        placeholder="Bu sonuca göre şu kaynağı da doğrula..."
                        value={followUp}
                        onChange={(e) => setFollowUp(e.target.value)}
                        maxLength={4000}
                      />
                      <button
                        type="submit"
                        disabled={busy || anyRunning || followUp.trim().length < 2 || !provider}
                        className="btn-secondary btn-sm shrink-0"
                      >
                        Gönder
                      </button>
                    </div>
                  </label>
                </form>
              )}
            </section>
          )}
        </div>

        <aside className="space-y-3">
          <div className="panel">
            <div className="panel-head">
              <h2 className="panel-title">Oturumlar</h2>
              <span className="numeric text-[12px] text-ink-3">{sessions.length}</span>
            </div>
            {loading ? (
              <p className="flex items-center gap-2 px-4 py-6 text-[12.5px] text-ink-3">
                <Loader2 size={13} className="animate-spin" aria-hidden />
                Yükleniyor
              </p>
            ) : sessions.length === 0 ? (
              <p className="px-4 py-6 text-[12.5px] text-ink-3">
                Henüz araştırma yok. Yukarıdan ilk sorunuzu başlatın.
              </p>
            ) : (
              <ul className="divide-y divide-line">
                {sessionOptions.map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      onClick={() => {
                        setSelectedId(item.id);
                        setError(null);
                      }}
                      className={`block w-full px-4 py-3 text-left transition-colors hover:bg-hover ${
                        selectedId === item.id ? "bg-accent-soft/40" : ""
                      }`}
                    >
                      <span className="line-clamp-2 text-[12.5px] font-medium text-ink">
                        {item.title}
                      </span>
                      <span className="mt-1 flex items-center gap-2 text-[11.5px] text-ink-3">
                        <Status value={item.status} />
                        <span className="numeric">{item.createdLabel}</span>
                      </span>
                      {(item.status === "running" || item.status === "queued") && item.activity && (
                        <span className="mt-1 line-clamp-1 text-[11.5px] text-accent-ink">
                          {item.activity}
                        </span>
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <p className="px-1 text-[11.5px] leading-relaxed text-ink-3">
            Araştırma yalnızca herkese açık kaynakları okur. AI kayıtları havuzda ve
            tedarik zincirinde onay bekleyen öneri olarak işaretlenir.
          </p>
        </aside>
      </div>
    </div>
  );
}

export default function ResearchPage() {
  return (
    <Suspense fallback={<p className="text-[13px] text-ink-3">Yükleniyor</p>}>
      <ResearchPageInner />
    </Suspense>
  );
}
