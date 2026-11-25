import type { Metadata, Viewport } from "next";
import Link from "next/link";
import { SiteNav } from "./components/site-nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "Showis — Açık Kaynak İstihbarat İnceleme Kayıtları",
  description:
    "İsim, e-posta ve kullanıcı adından kamuya açık kaynaklarda hesap tespiti, cross-platform ilişkilendirme ve kanıt zinciri.",
};

/** Tek tema: siyah. Tarayıcı çubuğu da siyah. */
export const viewport: Viewport = {
  themeColor: "#000000",
  colorScheme: "dark",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="tr">
      {/* Google Fonts baglantisi kaldirildi: CSP (`style-src 'self'`) her sayfada
          blokluyordu ve konsola hata yaziyordu. Fontlar `font-sans` yiginiyla sistemden
          geliyor; ileride self-host veya CSP allowlist ile geri eklenebilir (T-352). */}
      <body className="min-h-screen bg-canvas font-sans text-ink antialiased">
        <header className="sticky top-0 z-20 border-b border-line bg-canvas">
          <SiteNav />
        </header>
        <main className="mx-auto w-full max-w-[1180px] px-5 py-8 lg:px-8">{children}</main>
        <footer className="mt-16 border-t border-line">
          <div className="mx-auto flex max-w-[1180px] flex-col gap-2 px-5 py-5 text-[12px] text-ink-3 sm:flex-row sm:items-center sm:justify-between lg:px-8">
            <p>
              Showis — yalnızca kamuya açık kaynaklar taranır; kullanım öncesi hukuki dayanak
              gereklidir.
            </p>
            <nav aria-label="Alt gezinme" className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1">
              <Link href="/" className="link inline-flex items-center max-sm:min-h-[40px]">
                Vakalar
              </Link>
              <Link href="/cases/new" className="link inline-flex items-center max-sm:min-h-[40px]">
                Yeni vaka
              </Link>
              <Link href="/settings" className="link inline-flex items-center max-sm:min-h-[40px]">
                Ayarlar
              </Link>
            </nav>
          </div>
        </footer>
      </body>
    </html>
  );
}
