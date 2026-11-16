import type { Metadata } from "next";
import Link from "next/link";
import { SiteNav } from "./components/site-nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "Showis OSINT — Açık Kaynak İstihbarat Platformu",
  description:
    "İsim, e-posta ve kullanıcı adından kamuya açık kaynaklarda hesap tespiti, cross-platform ilişkilendirme ve kanıt zinciri.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="tr">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="min-h-screen bg-slate-950 font-sans text-slate-200 antialiased">
        <header className="sticky top-0 z-20 border-b border-slate-800 bg-slate-950/85 backdrop-blur">
          <SiteNav />
        </header>
        <main className="mx-auto w-full max-w-7xl px-5 py-8 lg:px-8">{children}</main>
        <footer className="mt-20 border-t border-slate-800 bg-slate-950">
          <div className="mx-auto max-w-7xl px-5 py-10 lg:px-8">
            <div className="grid grid-cols-1 gap-8 md:grid-cols-4">
              <div className="md:col-span-2">
                <p className="text-sm font-semibold tracking-tight text-slate-100">
                  Showis <span className="text-blue-400">OSINT</span>
                </p>
                <p className="mt-2 max-w-md text-xs leading-relaxed text-slate-400">
                  Kamuya açık kaynaklarda hesap tespiti, cross-platform ilişkilendirme ve
                  kanıt zinciri üreten istihbarat platformu. Her bulgu skor kırılımı ve
                  kaynak kanıtı ile birlikte raporlanır.
                </p>
              </div>
              <div>
                <p className="section-title">Yasal Çerçeve</p>
                <ul className="mt-3 space-y-1.5 text-xs text-slate-400">
                  <li>Yalnızca herkese açık sayfalar ve API&apos;ler</li>
                  <li>Giriş duvarı ve erişim kontrolü aşılmaz</li>
                  <li>KVKK/GDPR uyumlu saklama ve silme</li>
                </ul>
              </div>
              <div>
                <p className="section-title">Platform</p>
                <ul className="mt-3 space-y-1.5 text-xs">
                  <li>
                    <Link href="/" className="text-slate-300 transition-colors hover:text-blue-400">
                      Vaka listesi
                    </Link>
                  </li>
                  <li>
                    <Link
                      href="/cases/new"
                      className="text-slate-300 transition-colors hover:text-blue-400"
                    >
                      Yeni vaka
                    </Link>
                  </li>
                  <li>
                    <Link
                      href="/settings"
                      className="text-slate-300 transition-colors hover:text-blue-400"
                    >
                      Sağlayıcı ayarları
                    </Link>
                  </li>
                </ul>
              </div>
            </div>
            <div className="mt-8 flex flex-col gap-2 border-t border-slate-800 pt-4 text-[11px] text-slate-500 sm:flex-row sm:items-center sm:justify-between">
              <span>Showis OSINT — kullanım öncesi hukuki dayanak gereklidir.</span>
              <span className="mono">çalışma zamanı: MVP · self-hosted</span>
            </div>
          </div>
        </footer>
      </body>
    </html>
  );
}
