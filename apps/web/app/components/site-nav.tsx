"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Crosshair } from "lucide-react";
import { ThemeToggle } from "./theme-toggle";

const LINKS = [
  { href: "/", label: "Vakalar" },
  { href: "/cases/new", label: "Yeni vaka" },
  { href: "/settings", label: "Ayarlar" },
];

export function SiteNav() {
  const pathname = usePathname();

  return (
    <nav className="mx-auto flex h-14 w-full max-w-[1180px] items-center justify-between gap-3 px-5 sm:gap-6 lg:px-8">
      <div className="flex items-center gap-4 sm:gap-7">
        <Link href="/" className="flex shrink-0 items-center gap-2.5">
          <span className="flex h-6 w-6 items-center justify-center text-accent">
            <Crosshair size={18} strokeWidth={2} aria-hidden />
          </span>
          <span className="text-[15px] font-semibold tracking-[-0.01em] text-ink">Showis</span>
          <span className="hidden h-4 w-px bg-line sm:block" aria-hidden />
          <span className="hidden text-[12px] text-ink-3 sm:block">İnceleme kayıtları</span>
        </Link>

        {/* gezinme: kutu değil, alt çizgili sekmeler */}
        <div className="flex items-center gap-4 sm:gap-5">
          {LINKS.map((l) => {
            const active =
              l.href === "/" ? pathname === "/" : pathname.startsWith(l.href);
            return (
              <Link
                key={l.href}
                href={l.href}
                aria-current={active ? "page" : undefined}
                className={`relative flex h-14 items-center whitespace-nowrap text-[13px] transition-colors ${
                  active
                    ? "font-medium text-ink after:absolute after:inset-x-0 after:bottom-[-1px] after:h-[2px] after:bg-accent"
                    : "text-ink-3 hover:text-ink"
                }`}
              >
                {l.label}
              </Link>
            );
          })}
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-3">
        <ThemeToggle />
        <span className="hidden items-center gap-1.5 text-[12px] text-ink-3 sm:flex">
          <span className="h-1.5 w-1.5 rounded-full bg-ok" aria-hidden />
          Motor çevrimiçi
        </span>
      </div>
    </nav>
  );
}
