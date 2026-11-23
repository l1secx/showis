import { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const API_URL = (process.env.API_URL || "http://localhost:8000").replace(/\/+$/, "");
const COOKIE = "showis_user_token";

/** Kullanıcı token'ını API'de doğrula ve HttpOnly oturum cookie'si kur. */
export async function POST(request: NextRequest) {
  let token = "";
  try {
    const body = await request.json();
    token = typeof body?.token === "string" ? body.token.trim() : "";
  } catch {
    return Response.json({ detail: "Geçersiz istek" }, { status: 400 });
  }
  if (!token) {
    return Response.json({ detail: "Token gerekli" }, { status: 422 });
  }

  const upstream = await fetch(`${API_URL}/api/v1/auth/me`, {
    headers: { "X-User-Token": token, Accept: "application/json" },
    cache: "no-store",
  }).catch(() => null);
  if (!upstream || !upstream.ok) {
    return Response.json(
      { detail: "Kullanıcı anahtarı geçersiz" },
      { status: upstream?.status === 403 ? 403 : 401 }
    );
  }
  const me = await upstream.json();
  const response = Response.json({ ok: true, user: me });
  const secure =
    request.headers.get("x-forwarded-proto") === "https" ||
    request.nextUrl.protocol === "https:";
  response.headers.append(
    "Set-Cookie",
    `${COOKIE}=${encodeURIComponent(token)}; Path=/; HttpOnly; SameSite=Lax; Max-Age=28800${
      secure ? "; Secure" : ""
    }`
  );
  return response;
}

export async function DELETE() {
  const response = Response.json({ ok: true });
  response.headers.append(
    "Set-Cookie",
    `${COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0`
  );
  return response;
}
