import type { NextConfig } from "next";

/**
 * API erişimi tamamen sunucu tarafındadır:
 *
 *   tarayıcı ──(aynı origin, relative)──▶ /api/v1/...  ──▶ app/api/[...path]/route.ts
 *                                                        └─▶ API_URL/api/v1/... + X-API-Token
 *
 * `SHOWIS_API_TOKEN` yalnız `app/api/[...path]/route.ts` içinde, sunucu sürecinde okunur ve
 * upstream isteğine eklenir. İstemciye (HTML/JS/bundle) hiçbir biçimde verilmez ve
 * `NEXT_PUBLIC_*` altında tanımlanmaz.
 *
 * Not: Next 15 rewrite şeması istek (upstream) header'ı eklemeyi desteklemez
 * (`invalid field: headers for route ...` → "Invalid rewrite found"), ayrıca rewrite
 * hedefi build sırasında sabitlenir. Bu yüzden `/api/*` için rewrite yerine route handler
 * kullanılır: token ve API_URL çalışma zamanında (runtime env) okunur, imaja gömülmez.
 * Burada `rewrites()` tanımlanmamalıdır; tanımlanırsa afterFiles rewrite'ı catch-all
 * route handler'dan önce eşleşir ve token eklenmez.
 */
const nextConfig: NextConfig = {
  output: "standalone",
};

export default nextConfig;
