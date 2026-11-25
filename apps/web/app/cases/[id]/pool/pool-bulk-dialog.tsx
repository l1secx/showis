"use client";

import { useEffect, useRef } from "react";
import { Loader2 } from "lucide-react";

/**
 * Onay diyaloğu: kaç kayıt ve hangi durum yazılacağını açıkça söyler.
 * Modal olduğundan sayfa kaydırması kilitlenir, Esc vazgeçer, odak ilk
 * eylem düğmesindedir. Karar yalnız kullanıcınındır; AI bu diyaloğu açmaz.
 */
export function PoolBulkDialog({
  open,
  status,
  count,
  busy,
  error,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  status: "confirmed" | "rejected";
  count: number;
  busy: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const confirmRef = useRef<HTMLButtonElement>(null);
  const busyRef = useRef(busy);

  useEffect(() => {
    busyRef.current = busy;
  }, [busy]);

  useEffect(() => {
    if (!open) return;
    const previousFocus =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    confirmRef.current?.focus();
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
      previousFocus?.focus();
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busyRef.current) {
        onCancel();
        return;
      }
      if (event.key !== "Tab") return;
      const dialog = confirmRef.current?.closest('[role="dialog"]');
      if (!dialog) return;
      const focusable = Array.from(
        dialog.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])'
        )
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  if (!open) return null;

  const label = status === "confirmed" ? "onaylanacak" : "reddedilecek";

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/70 p-4 sm:items-center"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onCancel();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="pool-bulk-title"
        aria-describedby="pool-bulk-desc"
        className="panel w-full max-w-md bg-overlay p-5"
      >
        <h2 id="pool-bulk-title" className="text-[15px] font-semibold text-ink">
          Toplu {status === "confirmed" ? "onay" : "ret"}
        </h2>
        <p id="pool-bulk-desc" className="mt-1.5 text-[13px] leading-relaxed text-ink-2">
          Seçilen <strong className="font-medium text-ink">{count}</strong> kayıt{" "}
          <strong className="font-medium text-ink">{label}</strong>. Bu işlem yalnız bu
          havuzdaki seçili kayıtların durumunu değiştirir; AI önerileri kendiliğinden
          onaylanmaz.
        </p>
        {error && <p className="mt-2 text-[12.5px] text-danger">{error}</p>}
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            className="btn-secondary btn-sm"
            onClick={onCancel}
            disabled={busy}
          >
            Vazgeç
          </button>
          <button
            ref={confirmRef}
            type="button"
            className={status === "confirmed" ? "btn-primary btn-sm" : "btn-danger btn-sm"}
            onClick={onConfirm}
            disabled={busy}
          >
            {busy && <Loader2 size={12} className="animate-spin" aria-hidden />}
            {status === "confirmed" ? "Onayla" : "Reddet"}
          </button>
        </div>
      </div>
    </div>
  );
}
