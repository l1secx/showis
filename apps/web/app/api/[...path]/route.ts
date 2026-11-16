import type { NextRequest } from "next/server";

/**
 * Sunucu tarafı API proxy'si.
 *
 * Tarayıcı → (aynı origin) `/api/v1/...` → bu route handler → `API_URL/api/v1/...`
 *
 * `SHOWIS_API_TOKEN` yalnız bu dosyada, sunucu sürecinde okunur ve upstream isteğine
 * `X-API-Token` olarak eklenir. Token hiçbir zaman istemciye (HTML/JS/bundle) gönderilmez;
 * `NEXT_PUBLIC_*` altında tanımlanmaz. Ayrıca istemciden gelen `authorization` /
 * `x-api-token` başlıkları silinir: tarayıcı kendi token'ını uyduramaz.
 *
 * SSE (`/api/v1/scans/{id}/events`) de buradan geçer: `EventSource` özel header
 * gönderemez, ama yanıt `upstream.body` olarak aynen akıtıldığı için token sunucu
 * tarafında eklenmiş olur ve canlı akış bozulmaz.
 */

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const API_URL = (process.env.API_URL || "http://localhost:8000").replace(/\/+$/, "");
const API_TOKEN = process.env.SHOWIS_API_TOKEN?.trim() || "";

/** Uçtan uca taşınmaması gereken hop-by-hop başlıklar. */
const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);

type RouteContext = { params: Promise<{ path: string[] }> };

async function proxy(request: NextRequest, context: RouteContext) {
  const { path } = await context.params;

  // `..` / `.` segmentleriyle /api dışına çıkmayı engelle (open-proxy koruması).
  if (path.some((segment) => segment === "." || segment === "..")) {
    return new Response("Geçersiz API yolu", { status: 400 });
  }

  const target = new URL(`${API_URL}/api/${path.map(encodeURIComponent).join("/")}`);
  target.search = request.nextUrl.search;

  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (HOP_BY_HOP.has(key)) return;
    headers.set(key, value);
  });
  headers.delete("host");
  // İstemcinin kimlik başlıklarına güvenme — yalnız sunucu tarafı token geçerli.
  headers.delete("authorization");
  headers.delete("x-api-token");
  // fetch yanıtı kendisi çözer; upstream sıkıştırmasını istemeyiz.
  headers.delete("accept-encoding");

  if (API_TOKEN) headers.set("X-API-Token", API_TOKEN);

  const method = request.method.toUpperCase();
  const body = method === "GET" || method === "HEAD" ? undefined : await request.arrayBuffer();

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method,
      headers,
      body,
      redirect: "manual",
      cache: "no-store",
    });
  } catch {
    return Response.json(
      { detail: "API'ye ulaşılamadı (servis kapalı veya yapılandırma hatalı)" },
      { status: 502 }
    );
  }

  const responseHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    if (HOP_BY_HOP.has(key)) return;
    if (key === "content-encoding" || key === "content-length") return;
    responseHeaders.set(key, value);
  });

  // SSE: ara katmanlar (Caddy/nginx) tamponlamasın, tarayıcı önbelleğe almasın.
  const contentType = upstream.headers.get("content-type") || "";
  if (contentType.includes("text/event-stream")) {
    responseHeaders.set("Cache-Control", "no-cache, no-transform");
    responseHeaders.set("X-Accel-Buffering", "no");
  }

  const noBody = upstream.status === 204 || upstream.status === 304 || method === "HEAD";

  return new Response(noBody ? null : upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

export {
  proxy as DELETE,
  proxy as GET,
  proxy as HEAD,
  proxy as OPTIONS,
  proxy as PATCH,
  proxy as POST,
  proxy as PUT,
};
