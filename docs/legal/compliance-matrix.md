# Showis OSINT — Uyumluluk Matrisi (Compliance Matrix)

| Kaynak / Platform | URL / Endpoint | Yöntem | robots.txt Durumu | Tier | Onay Durumu | Son Kontrol | Notlar / Kısıtlar |
|---|---|---|---|---|---|---|---|
| **GitHub** | `https://api.github.com` | Resmî REST API | Uyumlu | **A** | ONAYLANDI | 2026-11-10 | BYOK Token ile 5.000 req/h; rate limit header takibi zorunlu. |
| **Brave Search** | `https://api.search.brave.com/res/v1/web/search` | Resmî REST API | Uyumlu | **A** | ONAYLANDI | 2026-11-10 | BYOK Token `X-Subscription-Token`. |
| **SerpAPI** | `https://serpapi.com/search` | Resmî REST API | Uyumlu | **A** | ONAYLANDI | 2026-11-10 | BYOK Query param `api_key`. |
| **Google CSE** | `https://www.googleapis.com/customsearch/v1` | Resmî REST API | Uyumlu | **A** | ONAYLANDI | 2026-11-10 | BYOK `cx` ve `key`. |
| **TryHackMe** | `https://tryhackme.com/p/<username>` | Headless Tarayıcı (Playwright) | `Allow: /` | **B** | ONAYLANDI (Kısıtlı) | 2026-11-10 | Vercel checkpoint / 429 algılanırsa blocked=True üretilir, SERP fallback devreye girer. Bypass yasak. |
| **HackTheBox (App)** | `https://app.hackthebox.com/profile/<id>` | Headless Tarayıcı (Playwright) | SPA (kural yok) | **B** | ONAYLANDI (Kısıtlı) | 2026-11-10 | JS render zorunlu; kullanıcı adı->id çözümü SERP öncelikli. |
| **HackTheBox Labs** | `https://labs.hackthebox.com` | HTTP / API | `Disallow: /` | **D (YASAK)** | REDDEDİLDİ | 2026-11-10 | KESİNLİKLE ÇAĞRILMAZ. Kodda ve runtime'da engelli. |
| **HackTheBox REST** | `https://www.hackthebox.com/api/` | REST / GraphQL | `Disallow: /api/` | **D (YASAK)** | REDDEDİLDİ | 2026-11-10 | KESİNLİKLE ÇAĞRILMAZ. |
