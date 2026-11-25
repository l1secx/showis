/** @type {import('tailwindcss').Config} */

/**
 * Renk paleti `app/globals.css` içindeki semantik tokenlardan okunur.
 * Sayfalar `slate-900` gibi kademe adlarıyla değil, anlamsal adlarla
 * (`surface`, `ink-2`, `line`, `accent`) yazılır; palet değişirse tek yer
 * değişir. RGB kanal üçlüsü sayesinde Tailwind'in `/50` opaklık
 * değiştiricileri (`border-accent/30`) çalışır.
 *
 * Tek tema vardır: siyah. Zemin ve yüzey `0 0 0`; panel ayrımı 1px çizgi.
 */
const token = (name) => `rgb(var(--${name.replace(/^--/, "")}) / <alpha-value>)`;

module.exports = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        canvas: token("--canvas"),
        surface: token("--surface"),
        sunken: token("--sunken"),
        overlay: token("--overlay"),
        hover: token("--hover"),

        ink: {
          DEFAULT: token("--ink"),
          2: token("--ink-2"),
          3: token("--ink-3"),
          4: token("--ink-4"),
        },

        line: {
          DEFAULT: token("--line"),
          2: token("--line-2"),
        },

        accent: {
          DEFAULT: token("--accent"),
          hover: token("--accent-hover"),
          soft: token("--accent-soft"),
          line: token("--accent-line"),
          ink: token("--accent-ink"),
          fg: token("--on-accent"),
        },

        ok: {
          DEFAULT: token("--ok"),
          soft: token("--ok-soft"),
          line: token("--ok-line"),
        },
        warn: {
          DEFAULT: token("--warn"),
          soft: token("--warn-soft"),
          line: token("--warn-line"),
        },
        danger: {
          DEFAULT: token("--danger"),
          soft: token("--danger-soft"),
          line: token("--danger-line"),
        },

        "on-accent": token("--on-accent"),
      },
      fontFamily: {
        sans: ["var(--font-sans)"],
        mono: ["var(--font-mono)"],
      },
      fontSize: {
        // Tipografi ölçeği — 12px tabanlı, analist aracı yoğunluğu.
        // 12px'in altına inilmez: küçük metin yüksek DPI ekranlarda okunmaz.
        "2xs": ["11px", { lineHeight: "1.4" }],
        xs: ["12px", { lineHeight: "1.45" }],
        sm: ["13px", { lineHeight: "1.5" }],
        base: ["13.5px", { lineHeight: "1.55" }],
      },
      borderRadius: {
        panel: "6px",
        control: "4px",
      },
      boxShadow: {
        // Yükselti çizgiyle anlatılır; gölge yalnızca yüzer popover'da.
        // `scrim` saydamdır: seçili segment gibi eski yüzeyler gölge taşımaz.
        popover: "var(--shadow-popover)",
        scrim: "var(--shadow-scrim)",
        lg: "0 0 #0000",
      },
      maxWidth: {
        prose: "68ch",
      },
      keyframes: {
        "panel-in": {
          from: { opacity: "0", transform: "translateY(4px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "panel-in": "panel-in 220ms cubic-bezier(0.2, 0.8, 0.3, 1) both",
      },
    },
  },
  plugins: [],
};
