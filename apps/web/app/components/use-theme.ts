"use client";

import { useCallback, useEffect, useState } from "react";

export type Theme = "light" | "dark";

/** Tercih anahtarı — hem `<head>` betiği hem bileşen aynı anahtarı kullanır. */
export const THEME_KEY = "showis:theme";

/** Tema değişimini duyan bileşenlere bildirir. */
export const THEME_EVENT = "showis:themechange";

export function readTheme(): Theme {
  if (typeof document === "undefined") return "light";
  return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
}

/**
 * Etkin tema. Sunucu ile istemci arasında tema farkı oluşmasın diye ilk
 * render'da `light` döner; `<head>` betiği zaten `data-theme`'i boyamadan
 * önce ayarladığı için ekranda yanıp sönme olmaz.
 */
export function useTheme(): Theme {
  const [theme, setTheme] = useState<Theme>("light");

  useEffect(() => {
    setTheme(readTheme());
    const onChange = () => setTheme(readTheme());
    window.addEventListener(THEME_EVENT, onChange);
    return () => window.removeEventListener(THEME_EVENT, onChange);
  }, []);

  return theme;
}

/** Temayı değiştirir ve tercihi saklar. */
export function useThemeToggle(): {
  theme: Theme;
  toggle: () => void;
  setTheme: (next: Theme) => void;
} {
  const theme = useTheme();

  const apply = useCallback((next: Theme) => {
    document.documentElement.setAttribute("data-theme", next);
    try {
      window.localStorage.setItem(THEME_KEY, next);
    } catch {
      /* özel sekme / kısıtlı depolama: tercih kaydedilmez, oturum içinde çalışır */
    }
    window.dispatchEvent(new Event(THEME_EVENT));
  }, []);

  const setTheme = useCallback((next: Theme) => apply(next), [apply]);
  const toggle = useCallback(
    () => apply(readTheme() === "dark" ? "light" : "dark"),
    [apply]
  );

  return { theme, toggle, setTheme };
}
