import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const API_URL = (process.env.API_URL || "http://localhost:8000").replace(/\/+$/, "");

/** Tarayıcı için sade sağlık yanıtı: upstream detayı/token'ı istemciye taşınmaz. */
export async function GET() {
  try {
    const res = await fetch(`${API_URL}/healthz`, {
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    return NextResponse.json(
      { ok: res.ok, status: res.ok ? "online" : "offline" },
      { status: res.ok ? 200 : 503, headers: { "Cache-Control": "no-store" } }
    );
  } catch {
    return NextResponse.json(
      { ok: false, status: "offline" },
      { status: 503, headers: { "Cache-Control": "no-store" } }
    );
  }
}
