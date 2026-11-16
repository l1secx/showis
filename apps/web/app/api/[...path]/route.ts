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
 *
 * İptal (T-342/T-355): upstream `fetch` bir `AbortController` ile çalışır. Sinyal iki
 * kaynaktan gelir: (a) istemci isteğinin iptali (`request.signal`), (b) yanıt gövdesini
 * pompalayan `ReadableStream`'in `cancel()`'ı — istemci okumayı bıraktığında (SSE sekmesi
 * kapanınca) upstream okuyucusu ve fetch iptal edilir; API tarafındaki `finally` çalışır,
 * bağlantı slotu sızmaz (T-351'de görülen 429).
 *
 * Gövde (T-342): `Content-Length` 10 MB'ı aşarsa istek hiç okunmadan 413 döner; gövde
 * tamponlanmadan (`duplex: "half"`) akıtılır ve akış sırasında da 10 MB tavanı uygulanır.
 */

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const API_URL = (process.env.API_URL || "http://localhost:8000").replace(/\/+$/, "");
const API_TOKEN = process.env.SHOWIS_API_TOKEN?.trim() || "";

/** Caddy'nin 10 MB tavanıyla aynı — proxy tarafında savunma katmanı. */
const MAX_BODY_BYTES = 10 * 1024 * 1024;

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
  // undici (fetch) 100-continue akışını desteklemez: istemcinin `Expect` başlığı
  // upstream'e taşınırsa istek "expect header not supported" ile düşer (T-342).
  "expect",
]);

type RouteContext = { params: Promise<{ path: string[] }> };
type FetchInitWithDuplex = RequestInit & { duplex?: "half" };

class BodyTooLargeError extends Error {
  constructor() {
    super("BODY_TOO_LARGE");
    this.name = "BodyTooLargeError";
  }
}

/** 10 MB üstü gövde: savunma katmanı (Caddy tavanı zaten var, bu ikinci kat). */
function payloadTooLarge(): Response {
  return Response.json(
    { detail: "İstek gövdesi çok büyük (üst sınır 10 MB)" },
    { status: 413 }
  );
}

/** undici hatayı `fetch failed` + `cause` zinciriyle sarar: tavan aşımını zincirde ara. */
function isBodyTooLarge(error: unknown, depth = 0): boolean {
  if (!error || depth > 4) return false;
  if (error instanceof BodyTooLargeError) return true;
  const message = error instanceof Error ? error.message : "";
  if (message.includes("BODY_TOO_LARGE")) return true;
  return isBodyTooLarge((error as { cause?: unknown }).cause, depth + 1);
}

/** İstemci kaynaklı iptal: AbortError, TimeoutError veya Next'in ResponseAborted'ı. */
function isAbortError(error: unknown, depth = 0): boolean {
  if (!error || depth > 4) return false;
  const name = error instanceof Error ? error.name : "";
  if (name === "AbortError" || name === "TimeoutError" || name === "ResponseAborted") {
    return true;
  }
  return isAbortError((error as { cause?: unknown }).cause, depth + 1);
}

/** Akışı sayarak geçirir; tavan aşılırsa akışı hataya düşürür (bellek şişmez). */
function limitedBodyStream(
  stream: ReadableStream<Uint8Array>
): ReadableStream<Uint8Array> {
  let total = 0;
  return stream.pipeThrough(
    new TransformStream<Uint8Array, Uint8Array>({
      transform(chunk, controller) {
        total += chunk.byteLength;
        if (total > MAX_BODY_BYTES) {
          controller.error(new BodyTooLargeError());
          return;
        }
        controller.enqueue(chunk);
      },
    })
  );
}

/**
 * T-355: yanıt gövdesini istemciye pompalar ve **iptali upstream'e yayar**.
 *
 * İstemci okumayı bıraktığında (SSE sekmesi kapanır, sayfadan çıkılır) Next bu
 * `ReadableStream`'in `cancel()`'ını çağırır. Burada upstream okuyucusu iptal edilir ve
 * fetch `AbortController`'ı abort edilir → API tarafındaki generator `finally` bloğu
 * çalışır, `_sse_connections` slotu serbest kalır (T-351'de görülen 429 sızıntısı).
 *
 * Chunk'lar geldiği gibi iletilir (tampon yok); SSE akışı bozulmaz.
 */
function pumpResponseBody(
  body: ReadableStream<Uint8Array>,
  controller: AbortController,
  label: string,
  isEventStream: boolean,
  onDone: () => void
): ReadableStream<Uint8Array> {
  const reader = body.getReader();
  let finished = false;

  const finish = () => {
    if (finished) return false;
    finished = true;
    onDone();
    return true;
  };

  const teardown = (reason: string) => {
    if (!finish()) return;
    void reader.cancel(reason).catch(() => undefined);
    if (!controller.signal.aborted) controller.abort();
    console.debug(
      "[api-proxy] upstream akışı kapatıldı:",
      label,
      `${isEventStream ? "sse" : "stream"}:${reason}`
    );
  };

  return new ReadableStream<Uint8Array>({
    async pull(streamController) {
      try {
        const { done, value } = await reader.read();
        if (done) {
          if (finish()) streamController.close();
          return;
        }
        if (value) streamController.enqueue(value);
      } catch (error) {
        if (!finish()) return; // zaten kapatıldı (istemci iptali)
        streamController.error(error);
      }
    },
    cancel(reason) {
      teardown(typeof reason === "string" ? reason : "client-cancel");
    },
  });
}

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
  const hasBody = method !== "GET" && method !== "HEAD";

  // Bildirilen boyut tavanı aşıyorsa gövdeyi hiç okumadan reddet.
  const declaredLength = Number(request.headers.get("content-length") ?? "");
  if (hasBody && Number.isFinite(declaredLength) && declaredLength > MAX_BODY_BYTES) {
    return payloadTooLarge();
  }

  // T-355: upstream iptali iki kaynaktan gelebilir — istemci isteği (request.signal)
  // veya yanıt akışının iptali (aşağıdaki pompa). Tek bir AbortController ikisini birleştirir.
  const controller = new AbortController();
  const abortFromClient = () => controller.abort();
  if (request.signal.aborted) abortFromClient();
  else request.signal.addEventListener("abort", abortFromClient, { once: true });
  const detachClientAbort = () => {
    request.signal.removeEventListener("abort", abortFromClient);
  };

  const init: FetchInitWithDuplex = {
    method,
    headers,
    redirect: "manual",
    cache: "no-store",
    signal: controller.signal,
  };

  if (hasBody) {
    if (request.body) {
      // T-342: tamponlamak yerine akıt (duplex: "half" — Node/undici streaming gövde).
      init.body = limitedBodyStream(request.body);
      init.duplex = "half";
    } else {
      init.body = new ArrayBuffer(0);
    }
  }

  let upstream: Response;
  try {
    upstream = await fetch(target, init);
  } catch (error) {
    detachClientAbort();
    if (isAbortError(error)) {
      // İstemci gitti: sessiz kapanış (log'a hata olarak yazma).
      console.debug("[api-proxy] istemci isteği iptal etti:", target.pathname);
      return new Response(null, { status: 499 });
    }
    // Akış sırasında tavan aşımı: undici hatayı `fetch failed` + cause olarak sarar.
    if (isBodyTooLarge(error)) {
      return payloadTooLarge();
    }
    console.error("[api-proxy] upstream hatası:", error);
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

  const isEventStream = contentType.includes("text/event-stream");
  const noBody =
    upstream.status === 204 || upstream.status === 304 || method === "HEAD" || !upstream.body;

  if (noBody) {
    detachClientAbort();
    return new Response(null, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders,
    });
  }

  // T-355: gövdeyi pompalarken iptali upstream'e yay (özellikle SSE).
  const body = pumpResponseBody(
    upstream.body as ReadableStream<Uint8Array>,
    controller,
    target.pathname,
    isEventStream,
    detachClientAbort
  );

  return new Response(body, {
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
