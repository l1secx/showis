"use client";

import Link from "next/link";

export default function ErrorPage({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div
      role="alert"
      className="rounded-[6px] border border-danger-line bg-danger-soft px-5 py-6 text-[13px] text-danger"
    >
      <h1 className="text-[15px] font-semibold">Bir hata oluştu</h1>
      <p className="mt-2 leading-relaxed">
        Sayfa yüklenirken beklenmeyen bir hata oluştu. İşlemi yeniden deneyebilirsiniz.
      </p>
      {error.digest && <p className="numeric mt-2 break-all text-[12px]">Hata kimliği: {error.digest}</p>}
      <div className="mt-4 flex flex-wrap gap-2">
        <button type="button" onClick={reset} className="btn-secondary btn-sm">
          Yeniden dene
        </button>
        <Link href="/" className="btn-quiet btn-sm">
          Vakalara dön
        </Link>
      </div>
    </div>
  );
}
