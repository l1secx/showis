/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        // Tek yerden tema: mevcut slate/blue kullanimlari yeni palete baglanir.
        slate: {
          50: "#f6f8fb",
          100: "#e9eef6",
          200: "#cfd9e6",
          300: "#a9b8cb",
          400: "#7d8ea6",
          500: "#5b6c84",
          600: "#3f4f66",
          700: "#26344a",
          800: "#161f2e",
          900: "#0b111b",
          950: "#05080e",
        },
        blue: {
          50: "#ecfeff",
          100: "#cffafe",
          200: "#a5f3fc",
          300: "#67e8f9",
          400: "#22d3ee",
          500: "#06b6d4",
          600: "#0891b2",
          700: "#0e7490",
          800: "#155e75",
          900: "#164e63",
          950: "#083344",
        },
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      boxShadow: {
        card: "0 1px 0 0 rgba(255,255,255,0.03) inset, 0 18px 40px -24px rgba(0,0,0,0.9)",
        glow: "0 0 0 1px rgba(34,211,238,0.25), 0 8px 30px -12px rgba(34,211,238,0.35)",
      },
      letterSpacing: {
        wider: "0.08em",
      },
    },
  },
  plugins: [],
};
