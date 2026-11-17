"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Crosshair, FolderSearch, PlusCircle, Settings2 } from "lucide-react";

const LINKS = [
  { href: "/", label: "Vakalar", icon: FolderSearch },
  { href: "/cases/new", label: "Yeni Vaka", icon: PlusCircle },
  { href: "/settings", label: "Ayarlar", icon: Settings2 },
];

export function SiteNav() {
  const pathname = usePathname();

  return (
    <nav className="mx-auto flex h-16 w-full max-w-7xl items-center gap-3 px-4 sm:gap-8 sm:px-5 lg:px-8">
      <Link href="/" className="group flex items-center gap-2.5">
        <span className="flex h-8 w-8 items-center justify-center rounded-lg border border-blue-500/30 bg-blue-500/10 text-blue-400">
          <Crosshair size={17} strokeWidth={2.2} />
        </span>
        <span className="leading-tight">
          <span className="block text-[15px] font-semibold tracking-tight text-slate-100">
            Showis
          </span>
          <span className="block font-mono text-[10px] uppercase tracking-wider text-slate-500">
            osint platform
          </span>
        </span>
      </Link>

      <div className="flex items-center gap-1">
        {LINKS.map((l) => {
          const active = l.href === "/" ? pathname === "/" : pathname.startsWith(l.href);
          const Icon = l.icon;
          return (
            <Link
              key={l.href}
              href={l.href}
              aria-label={l.label}
              title={l.label}
              className={`flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm transition-colors sm:px-3 ${
                active
                  ? "bg-slate-800/80 text-slate-100"
                  : "text-slate-400 hover:bg-slate-900 hover:text-slate-200"
              }`}
            >
              <Icon size={15} className={active ? "text-blue-400" : ""} />
              {/* 375px'te baslik tasmasin: mobilde yalniz ikon + aria-label */}
              <span className="hidden sm:inline">{l.label}</span>
            </Link>
          );
        })}
      </div>

      <div className="ml-auto flex items-center gap-2">
        <span className="chip hidden sm:inline-flex">
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
          kamuya açık kaynaklar
        </span>
        <span className="kbd hidden md:inline">MVP</span>
      </div>
    </nav>
  );
}
