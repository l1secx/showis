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

function readSession(key: string): string | null {
  if (typeof window === "undefined") return null;
  return window.sessionStorage.getItem(key);
}

export function ScanSessionProvider({
  caseId,
  children,
}: {
  caseId: string;
  children: React.ReactNode;
}) {
  const [scanId, setScanId] = useState<string | null>(() => readSession(scanKey(caseId)));
  const [scan, setScan] = useState<ScanStatus | null>(null);
  const [events, setEvents] = useState<SseEvent[]>([]);
  const [mode, setModeState] = useState(() => readSession(modeKey(caseId)) || "O2");
  const [estimate, setEstimate] = useState<ScanEstimate | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [findingsVersion, setFindingsVersion] = useState(0);

  const esRef = useRef<EventSource | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refreshScan = useCallback(async (id: string) => {
    try {
      setScan(await api.getScan(id));
    } catch {
      /* gecici hata: sonraki turda tekrar denenir */
    }
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
        setEvents((prev) =>
          [...prev.slice(-299), { type, data, at: new Date().toLocaleTimeString("tr-TR") }]
        );
        if (type === "finding.created") setFindingsVersion((v) => v + 1);
        if (type.startsWith("scan.") || type === "finding.created") refreshScan(id);
      };
      EVENT_TYPES.forEach((t) => es.addEventListener(t, push(t) as EventListener));
      es.onerror = () => es.close();
    },
    [refreshScan]
  );

  const stopTimers = useCallback(() => {
    esRef.current?.close();
    esRef.current = null;
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = null;
  }, []);

  // Sayfa yenilendiginde / sekmeye donuldugunde oturumu geri yukle.
  useEffect(() => {
    if (!scanId) return;
    refreshScan(scanId);
    subscribe(scanId);
    pollRef.current = setInterval(() => refreshScan(scanId), 3000);
    return stopTimers;
  }, [scanId, refreshScan, subscribe, stopTimers]);

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

  const setMode = (next: string) => {
    setModeState(next);
    if (typeof window !== "undefined") window.sessionStorage.setItem(modeKey(caseId), next);
  };

  const start = async () => {
    setStarting(true);
    setError(null);
    setEvents([]);
    try {
      const res = await api.startScan(caseId, { mode });
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
      estimate,
      starting,
      error,
      findingsVersion,
      start,
      cancel,
      refresh: () => scanId && refreshScan(scanId),
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [caseId, scanId, scan, events, mode, estimate, starting, error, findingsVersion]
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
