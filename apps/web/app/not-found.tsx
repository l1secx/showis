import Link from "next/link";

export default function NotFound() {
  return (
    <div className="panel mx-auto max-w-xl px-6 py-12 text-center">
      <p className="numeric text-[12px] text-ink-3">404</p>
      <h1 className="page-title mt-2">Sayfa bulunamadı</h1>
      <p className="mt-2 text-[13px] leading-relaxed text-ink-3">
        Aradığınız kayıt taşınmış, silinmiş veya adres yanlış yazılmış olabilir.
      </p>
      <div className="mt-5 flex flex-wrap justify-center gap-2">
        <Link href="/" className="btn-primary btn-sm">
          Vakalara dön
        </Link>
        <Link href="/cases/new" className="btn-secondary btn-sm">
          Yeni vaka
        </Link>
      </div>
    </div>
  );
}
