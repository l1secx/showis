import type { Metadata } from "next";
import Link from "next/link";
import { SiteNav } from "./components/site-nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "Showis — Açık Kaynak İstihbarat İnceleme Kayıtları",
  description:
    "İsim, e-posta ve kullanıcı adından kamuya açık kaynaklarda hesap tespiti, cross-platform ilişkilendirme ve kanıt zinciri.",
};

/**
 * Tema başlangıcı — ilk boyamadan önce çalışmalı, yoksa sayfa açık temayla
 * açılıp koyuya döner (flash). Tercih localStorage'da; yoksa işletim
 * sistemi tercihine uyulur.
 */
const THEME_BOOTSTRAP = `(function(){try{var s=localStorage.getItem("showis:theme");var t=(s==="light"||s==="dark")?s:(window.matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light");document.documentElement.setAttribute("data-theme",t);}catch(e){document.documentElement.setAttribute("data-theme","light");}})();`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="tr" suppressHydrationWarning>
      {/* Google Fonts baglantisi kaldirildi: CSP (`style-src 'self'`) her sayfada
          blokluyordu ve konsola hata yaziyordu. Fontlar `font-sans` yiginiyla sistemden
          geliyor; ileride self-host veya CSP allowlist ile geri eklenebilir (T-352). */}
      <body className="min-h-screen bg-canvas font-sans text-ink antialiased">
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOTSTRAP }} />
        <header className="sticky top-0 z-20 border-b border-line bg-surface">
          <SiteNav />
        </header>
        <main className="mx-auto w-full max-w-[1180px] px-5 py-8 lg:px-8">{children}</main>
        <footer className="mt-16 border-t border-line bg-surface">
          <div className="mx-auto max-w-[1180px] px-5 py-9 lg:px-8">
            <div className="grid grid-cols-1 gap-8 sm:grid-cols-2 lg:grid-cols-4">
              <div className="lg:col-span-2">
                <p className="text-[13px] font-semibold text-ink">Showis</p>
                <p className="mt-2 max-w-md text-[12.5px] leading-relaxed text-ink-3">
                  Kamuya açık kaynaklarda hesap tespiti, çapraz platform ilişkilendirme ve kanıt
                  zinciri üreten inceleme platformu. Her bulgu, güven skorunun kırılımı ve
                  kaynak kanıtı ile birlikte kayda geçer.
                </p>
              </div>
              <div>
                <p className="text-[12px] font-medium text-ink">Yasal çerçeve</p>
                <ul className="mt-2.5 space-y-1.5 text-[12.5px] leading-relaxed text-ink-3">
                  <li>Yalnızca herkese açık sayfalar ve API&apos;ler taranır</li>
                  <li>Giriş duvarı ve erişim kontrolü aşılmaz</li>
                  <li>KVKK ve GDPR uyumlu saklama ve silme</li>
                </ul>
              </div>
              <div>
                <p className="text-[12px] font-medium text-ink">Kayıtlar</p>
                <ul className="mt-2.5 space-y-1.5 text-[12.5px]">
                  <li>
                    <Link href="/" className="link">
                      Vaka listesi
                    </Link>
                  </li>
                  <li>
                    <Link href="/cases/new" className="link">
                      Yeni vaka
                    </Link>
                  </li>
                  <li>
                    <Link href="/settings" className="link">
                      Sağlayıcı ayarları
                    </Link>
                  </li>
                </ul>
              </div>
            </div>
            <div className="mt-8 flex flex-col gap-1.5 border-t border-line pt-4 text-[12px] text-ink-3 sm:flex-row sm:items-center sm:justify-between">
              <span>Showis — kullanım öncesi hukuki dayanak gereklidir.</span>
              <span className="text-[12px]">MVP sürümü, self-hosted</span>
            </div>
          </div>
        </footer>
      </body>
    </html>
  );
}
