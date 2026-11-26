"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { api, type ScanEstimate, type ScanStatus } from "../../../lib/api";
import { normalizeScanMode } from "../../../lib/scan-modes";

/** Sekmeler arasinda gezinirken kaybolmamasi gereken tarama oturumu. */

export interface SseEvent {
  type: string;
  data: any;
  at: string;
}

const EVENT_TYPES = [
  "scan.status",
  "scan.tasks_planned",
  "task.started",
  "observation.created",
  "task.finished",
  "task.failed",
  "finding.created",
  "scan.completed",
  "scan.failed",
  "scan.cancelled",
];

interface ScanSession {
  caseId: string;
  scanId: string | null;
  scan: ScanStatus | null;
  events: SseEvent[];
  mode: string;
  setMode: (mode: string) => void;
  availableModels: string[];
  llmModel: string;
  setLlmModel: (model: string) => void;
  estimate: ScanEstimate | null;
  starting: boolean;
  error: string | null;
  /** Bulgular sekmesinin yeniden yuklenmesi icin sayac. */
  findingsVersion: number;
  start: () => Promise<void>;
  cancel: () => Promise<void>;
  refresh: () => void;
}

const ScanSessionContext = createContext<ScanSession | null>(null);

export function useScanSession(): ScanSession {
  const ctx = useContext(ScanSessionContext);
  if (!ctx) throw new Error("useScanSession, ScanSessionProvider içinde kullanılmalı");
  return ctx;
}

const scanKey = (caseId: string) => `showis:scan:${caseId}`;
const modeKey = (caseId: string) => `showis:mode:${caseId}`;
const modelKey = (caseId: string) => `showis:llm-model:${caseId}`;

function readSession(key: string): string | null {
  if (typeof window === "undefined") return null;
  return window.sessionStorage.getItem(key);
}

export function ScanSessionProvider({
  caseId,
  initialMode,
  children,
}: {
  caseId: string;
  /** Yeni vaka akışından gelen mod; sessionStorage'daki değerden önceliklidir. */
  initialMode?: string;
  children: React.ReactNode;
}) {
  const [scanId, setScanId] = useState<string | null>(() => readSession(scanKey(caseId)));
  const [scan, setScan] = useState<ScanStatus | null>(null);
  const [events, setEvents] = useState<SseEvent[]>([]);
  const [mode, setModeState] = useState<string>(
    () =>
      normalizeScanMode(initialMode) ??
      normalizeScanMode(readSession(modeKey(caseId))) ??
      "O2"
  );
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [llmModel, setLlmModelState] = useState(() => readSession(modelKey(caseId)) || "");
  const [estimate, setEstimate] = useState<ScanEstimate | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [findingsVersion, setFindingsVersion] = useState(0);

  const esRef = useRef<EventSource | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const refreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const eventFlushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingEventsRef = useRef<SseEvent[]>([]);

  // URL'den gelen mod sonradan değişirse uygula; storage'daki değer korunur.
  useEffect(() => {
    const next = normalizeScanMode(initialMode);
    if (!next) return;
    setModeState((current) => (current === next ? current : next));
    if (typeof window !== "undefined") window.sessionStorage.setItem(modeKey(caseId), next);
  }, [caseId, initialMode]);

  const refreshScan = useCallback(async (id: string) => {
    try {
      setScan(await api.getScan(id));
    } catch {
      /* gecici hata: sonraki turda tekrar denenir */
    }
  }, []);

  /** SSE olay seli: tam scan durumunu her olayda çekmek yerine 750 ms'de bir çek. */
  const scheduleRefresh = useCallback(
    (id: string) => {
      if (refreshTimerRef.current) return;
      refreshTimerRef.current = setTimeout(() => {
        refreshTimerRef.current = null;
        void refreshScan(id);
      }, 750);
    },
    [refreshScan]
  );

  /** Yüksek hacimli olayları 350 ms'lik partiler hâlinde tek render'a indir. */
  const flushEvents = useCallback((immediate = false) => {
    const flush = () => {
      eventFlushTimerRef.current = null;
      const batch = pendingEventsRef.current;
      pendingEventsRef.current = [];
      if (batch.length === 0) return;
      setEvents((prev) => [...prev.slice(-199), ...batch].slice(-200));
    };
    if (immediate) {
      if (eventFlushTimerRef.current) clearTimeout(eventFlushTimerRef.current);
      flush();
      return;
    }
    if (eventFlushTimerRef.current) return;
    eventFlushTimerRef.current = setTimeout(flush, 350);
  }, []);

  const subscribe = useCallback(
    (id: string) => {
      esRef.current?.close();
      const es = new EventSource(`/api/v1/scans/${id}/events`);
      esRef.current = es;
      const push = (type: string) => (ev: MessageEvent) => {
        let data: any = {};
        try {
          data = JSON.parse(ev.data);
        } catch {
          /* gecersiz JSON: yoksay */
        }
        pendingEventsRef.current.push({
          type,
          data,
          at: new Date().toLocaleTimeString("tr-TR"),
        });
        const terminal =
          type === "scan.completed" || type === "scan.failed" || type === "scan.cancelled";
        flushEvents(terminal);
        if (type === "finding.created") setFindingsVersion((v) => v + 1);
        if (type.startsWith("scan.") || type === "finding.created") {
          if (terminal) void refreshScan(id);
          else scheduleRefresh(id);
        }
      };
      EVENT_TYPES.forEach((t) => es.addEventListener(t, push(t) as EventListener));
      es.onerror = () => es.close();
    },
    [flushEvents, refreshScan, scheduleRefresh]
  );

  const stopTimers = useCallback(() => {
    esRef.current?.close();
    esRef.current = null;
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = null;
    if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);
    refreshTimerRef.current = null;
    if (eventFlushTimerRef.current) clearTimeout(eventFlushTimerRef.current);
    eventFlushTimerRef.current = null;
    pendingEventsRef.current = [];
  }, []);

  // Sayfa yenilendiginde / sekmeye donuldugunde oturumu geri yukle.
  useEffect(() => {
    if (!scanId) return;
    refreshScan(scanId);
    subscribe(scanId);
    pollRef.current = setInterval(() => refreshScan(scanId), 5000);
    return stopTimers;
  }, [scanId, refreshScan, subscribe, stopTimers]);

  // Terminal durumda SSE/polling gereksiz: bağlantıyı ve zamanlayıcıyı kapat.
  useEffect(() => {
    if (!scan) return;
    if (["completed", "failed", "cancelled"].includes(scan.status)) {
      esRef.current?.close();
      esRef.current = null;
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, [scan]);

  // Tahmini sure: mod degisince yeniden hesaplanir.
  useEffect(() => {
    let cancelled = false;
    api
      .estimateScan(caseId, mode)
      .then((e) => !cancelled && setEstimate(e))
      .catch(() => !cancelled && setEstimate(null));
    return () => {
      cancelled = true;
    };
  }, [caseId, mode]);

  // Aktif profilin izinli modelleri; tarama başlatırken seçilir.
  useEffect(() => {
    let cancelled = false;
    api
      .listAIProviders()
      .then((providers) => {
        if (cancelled) return;
        const active = providers.find((provider) => provider.is_active && provider.enabled);
        const config = active?.config || {};
        const raw = Array.isArray(config.models) ? config.models : [];
        const models = raw
          .map((model: unknown) => String(model))
          .filter((model: string) => model.trim());
        const fallback = String(config.model || "");
        const list = models.length ? models : fallback ? [fallback] : [];
        setAvailableModels(list);
        setLlmModelState((current) =>
          current && list.includes(current) ? current : list[0] || ""
        );
      })
      .catch(() => !cancelled && setAvailableModels([]));
    return () => {
      cancelled = true;
    };
  }, [caseId]);

  const setMode = (next: string) => {
    setModeState(next);
    if (typeof window !== "undefined") window.sessionStorage.setItem(modeKey(caseId), next);
  };

  const setLlmModel = (next: string) => {
    setLlmModelState(next);
    if (typeof window !== "undefined") window.sessionStorage.setItem(modelKey(caseId), next);
  };

  const start = async () => {
    setStarting(true);
    setError(null);
    setEvents([]);
    try {
      const res = await api.startScan(caseId, {
        mode,
        llm_model: llmModel || undefined,
      });
      if (typeof window !== "undefined") window.sessionStorage.setItem(scanKey(caseId), res.scan_id);
      setScanId(res.scan_id);
      subscribe(res.scan_id);
      await refreshScan(res.scan_id);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setStarting(false);
    }
  };

  const cancel = async () => {
    if (!scanId) return;
    try {
      await api.cancelScan(scanId);
      await refreshScan(scanId);
    } catch (e: any) {
      setError(e.message);
    }
  };

  const value = useMemo<ScanSession>(
    () => ({
      caseId,
      scanId,
      scan,
      events,
      mode,
      setMode,
      availableModels,
      llmModel,
      setLlmModel,
      estimate,
      starting,
      error,
      findingsVersion,
      start,
      cancel,
      refresh: () => scanId && refreshScan(scanId),
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      caseId,
      scanId,
      scan,
      events,
      mode,
      availableModels,
      llmModel,
      estimate,
      starting,
      error,
      findingsVersion,
    ]
  );

  return <ScanSessionContext.Provider value={value}>{children}</ScanSessionContext.Provider>;
}

/** "1 dk 37 sn" biciminde kisa sure etiketi. */
export function formatDuration(totalSeconds: number): string {
  const s = Math.max(0, Math.round(totalSeconds));
  if (s < 60) return `${s} sn`;
  const m = Math.floor(s / 60);
  const rest = s % 60;
  if (m < 60) return rest ? `${m} dk ${rest} sn` : `${m} dk`;
  const h = Math.floor(m / 60);
  return `${h} sa ${m % 60} dk`;
}
