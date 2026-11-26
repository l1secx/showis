"use client";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="tr">
      <body style={{ background: "#000", color: "#e5e7eb", fontFamily: "system-ui, sans-serif" }}>
        <main style={{ margin: "0 auto", maxWidth: 520, padding: 32 }}>
          <h1 style={{ fontSize: 18 }}>Bir hata oluştu</h1>
          <p style={{ lineHeight: 1.6, opacity: 0.8 }}>
            Uygulama yüklenirken beklenmeyen bir hata oluştu.
          </p>
          {error.digest && (
            <p style={{ fontSize: 12, opacity: 0.6, wordBreak: "break-all" }}>
              Hata kimliği: {error.digest}
            </p>
          )}
          <button
            type="button"
            onClick={reset}
            style={{
              marginTop: 16,
              padding: "8px 14px",
              borderRadius: 6,
              border: "1px solid #444",
              background: "#1a1a1a",
              color: "#e5e7eb",
              cursor: "pointer",
            }}
          >
            Yeniden dene
          </button>
        </main>
      </body>
    </html>
  );
}
