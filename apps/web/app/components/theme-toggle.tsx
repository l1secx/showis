"use client";

import { Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";
import { useThemeToggle } from "./use-theme";

/**
 * Tema anahtarı. Karanlık temada "güneş" (şu anki tema) gösterilir —
 * düğme bir sonraki durumu değil, mevcut durumu bildirir.
 *
 * Tema, `<head>` betiğiyle ilk boyamadan önce uygulanır; ancak React'in
 * ilk render'ı sunucu anlık görüntüsüyle ("light") yapılır. Bu yüzden
 * mount edilene kadar **yanlış** bir ikon ve etiket göstermemek adına
 * nötr bir yer tutucu kullanılır: mount'tan önce düğme ne "koyuya geç"
 * ne "açığa geç" iddiasında bulunur.
 */
export function ThemeToggle() {
  const { theme, toggle } = useThemeToggle();
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);

  const isDark = theme === "dark";

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={mounted ? (isDark ? "Açık temaya geç" : "Koyu temaya geç") : "Temayı değiştir"}
      title={mounted ? (isDark ? "Açık tema" : "Koyu tema") : "Tema"}
      className="flex h-8 w-8 items-center justify-center rounded-[6px] text-ink-3 transition-colors hover:bg-hover hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/25 focus-visible:ring-offset-1 focus-visible:ring-offset-canvas"
    >
      {mounted ? (
        isDark ? (
          <Sun size={15} aria-hidden />
        ) : (
          <Moon size={15} aria-hidden />
        )
      ) : (
        <span className="h-[15px] w-[15px]" aria-hidden />
      )}
    </button>
  );
}
