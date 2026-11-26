from __future__ import annotations

import asyncio
import contextvars
import hashlib
import logging
import os
import random
import re
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import httpx
from showis_contracts import Evidence

from .netguard import pinned_async_transport
from .policy import SSRFViolationError, guard_url
from .ratelimit import HostLimiter, default_host_limiter, limiter_enabled

logger = logging.getLogger("showis.http")

#: Loglarda URL query'sinde maskelenecek parametreler (T-332)
_SENSITIVE_QUERY_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "api-key",
        "key",
        "token",
        "access_token",
        "refresh_token",
        "client_secret",
        "secret",
        "password",
        "cx",
        "subscription-key",
        "auth",
    }
)
_URL_MASK = "****"


def redact_url(url: str) -> str:
    """URL'yi loglamak icin guvenli hale getirir (hassas query degerleri maskeli).

    `api_key`, `key`, `token`, `access_token`, `cx` gibi parametrelerin degeri
    `****` olur; host/yol ve diger parametreler teshis icin korunur.
    """
    if not url:
        return url
    try:
        parts = urlparse(url)
    except ValueError:  # pragma: no cover - bozuk URL
        return "<gecersiz-url>"
    if not parts.scheme and not parts.netloc:
        return url
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    safe = [
        (key, _URL_MASK if key.lower() in _SENSITIVE_QUERY_KEYS else value)
        for key, value in pairs
    ]
    return urlunparse(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            parts.params,
            urlencode(safe, doseq=True).replace("%2A%2A%2A%2A", _URL_MASK),
            parts.fragment,
        )
    )

DEFAULT_UA = "Showis/0.1 (+https://github.com/showis-osint)"
MAX_REDIRECTS = 5

#: Surec genelindeki toplam sayac (geriye donuk uyumluluk).
_requests_made = 0
#: Butce nedeniyle reddedilen istek sayaci (surec omurlu; /metrics icin — T-329).
_budget_exceeded = 0


def budget_exceeded_count() -> int:
    """Surec omurlu: butce tukendigi icin reddedilen istek sayisi (T-329)."""
    return _budget_exceeded


def requests_made() -> int:
    """Surec genelinde atilan gercek HTTP istek sayisi (butce sayaci).

    Paralel yurutmede delta hesabi icin kullanmayin: es zamanli gorevler ayni
    sayaci paylasir. Gorev-bazli sayim icin ``count_requests_scope()`` /
    ``requests_since()`` kullanin.
    """
    return _requests_made


class RequestScope:
    """Gorev-bazli istek sayaci tasiyicisi (contextvar uzerinden izole)."""

    __slots__ = ("count",)

    def __init__(self) -> None:
        self.count = 0

    def record(self) -> None:
        self.count += 1


#: Aktif istek kapsami. ``contextvars`` sayesinde her asyncio gorevi kendi
#: kopyasini gorur; paralel gorevler birbirinin sayacini bozamaz.
_request_scope: contextvars.ContextVar[RequestScope | None] = contextvars.ContextVar(
    "showis_request_scope", default=None
)


def current_request_scope() -> RequestScope | None:
    """Gecerli baglamdaki istek kapsamini dondurur (yoksa ``None``)."""
    return _request_scope.get()


def request_counter() -> int:
    """Gecerli baglamda sayilan istek sayisi (kapsam yoksa 0)."""
    scope = _request_scope.get()
    return scope.count if scope is not None else 0


def request_marker() -> int:
    """``requests_since()`` icin isaret koyar (gorev basi)."""
    return request_counter()


def requests_since(marker: int) -> int:
    """Isaretten bu yana bu baglamda atilan istek sayisi."""
    return max(0, request_counter() - marker)


@contextmanager
def count_requests_scope() -> Iterator[RequestScope]:
    """Bu baglamda atilan istekleri ayri sayar (gorev-bazli butce olcumu).

    Kullanim::

        with count_requests_scope() as scope:
            await connector.run(spec, ctx)
        used = scope.count

    Kapsam, olusturuldugu asyncio gorevine aittir: ``asyncio.create_task`` ile
    baslatilan alt gorevler kendi kapsamlarini kurmalidir (scanner tam olarak
    bunu yapar), aksi halde ust baglamin kapsamini paylasirlar.
    """
    scope = RequestScope()
    token = _request_scope.set(scope)
    try:
        yield scope
    finally:
        _request_scope.reset(token)


def _record_request() -> None:
    """Hem surec genelini hem de varsa gorev-bazli kapsami artirir."""
    global _requests_made
    _requests_made += 1
    scope = _request_scope.get()
    if scope is not None:
        scope.record()


class BudgetExceededError(RuntimeError):
    """HTTP istek butcesi tukendi — istek **atilmadan** yukseltilir (fail-closed)."""


class RequestBudget:
    """Tarama-genelinde HTTP istek butcesi (paralel gorevler ayni nesneyi paylasir).

    `contextvar` bir *nesne referansi* tasidigi icin alt gorevler ayni
    `RequestBudget`'i gorur: paralel gorevlerin istekleri tek bir sayacta
    toplanir ve limit asildiginda kimse istek atamaz.
    """

    __slots__ = ("max_requests", "used")

    def __init__(self, max_requests: int):
        self.max_requests = max(0, int(max_requests))
        self.used = 0

    @property
    def remaining(self) -> int:
        return max(0, self.max_requests - self.used)

    def consume(self) -> None:
        """Bir istek hakki tuketir; butce bittiyse `BudgetExceededError`."""
        global _budget_exceeded
        if self.used >= self.max_requests:
            _budget_exceeded += 1
            raise BudgetExceededError(
                f"HTTP istek butcesi tukendi (max_http_requests={self.max_requests})"
            )
        self.used += 1


_request_budget: contextvars.ContextVar[RequestBudget | None] = contextvars.ContextVar(
    "showis_request_budget", default=None
)


def set_request_budget(max_requests: int) -> contextvars.Token[RequestBudget | None]:
    """Bu baglam (ve alt gorevleri) icin istek butcesi kurar; token ile geri alinir."""
    return _request_budget.set(RequestBudget(max_requests))


def reset_request_budget(token: contextvars.Token[RequestBudget | None]) -> None:
    _request_budget.reset(token)


def current_request_budget() -> RequestBudget | None:
    return _request_budget.get()


def _consume_request_budget() -> None:
    guard = _request_budget.get()
    if guard is not None:
        guard.consume()



def _redact_error(error: object | None) -> str:
    """Hata metninden URL query sirlarini ayiklar (log/DB'ye sizmasin)."""
    text = str(error)
    for key in _SENSITIVE_QUERY_KEYS:
        text = re.sub(
            rf"(?i)([?&]{re.escape(key)}=)([^&\s\"']+)",
            rf"\1{_URL_MASK}",
            text,
        )
    return text


class RobotsCache:
    #: robots.txt alınamadığında (5xx/429/ağ hatası) yeniden deneme aralığı (sn).
    #: RFC 9309 §2.3.1.4: erişilemeyen robots.txt "tamamen yasak" sayılır.
    DEFAULT_FAILURE_RETRY_SECONDS = 300.0

    def __init__(
        self,
        user_agent: str = DEFAULT_UA,
        *,
        failure_retry_seconds: float = DEFAULT_FAILURE_RETRY_SECONDS,
    ):
        self.user_agent = user_agent
        self.failure_retry_seconds = max(1.0, float(failure_retry_seconds))
        self._parsers: dict[str, RobotFileParser] = {}
        #: origin -> fail-closed kararının geçerlilik sonu (monotonic).
        self._blocked_until: dict[str, float] = {}

    async def _fetch_parser(self, origin: str) -> RobotFileParser:
        """robots.txt getirir; hata durumunda **fail-closed** parser döndürür."""
        robots_url = f"{origin}/robots.txt"
        parser = RobotFileParser()
        current = robots_url
        try:
            async with httpx.AsyncClient(
                timeout=5.0,
                headers={"User-Agent": self.user_agent},
                transport=pinned_async_transport(),
            ) as client:
                for _hop in range(MAX_REDIRECTS + 1):
                    # Yönlendirme hedefi de SSRF kapısından geçer.
                    await asyncio.to_thread(guard_url, current)
                    resp = await client.get(current)
                    location = resp.headers.get("location")
                    if resp.is_redirect and location:
                        current = str(resp.url.join(location))
                        continue
                    if resp.status_code == 200:
                        parser.parse(resp.text.splitlines())
                        return parser
                    if 400 <= resp.status_code < 500 and resp.status_code != 429:
                        # RFC 9309 §2.3.1.3: kaynak yok -> erişim serbest.
                        parser.parse(["User-agent: *", "Allow: /"])
                        return parser
                    break
        except Exception as exc:
            logger.warning(
                "robots.txt alinamadi (%s): %s — fail-closed",
                redact_url(robots_url),
                type(exc).__name__,
            )

        # 429, 5xx, çok fazla yönlendirme veya ağ hatası: tamamen yasak.
        parser.parse(["User-agent: *", "Disallow: /"])
        self._blocked_until[origin] = time.monotonic() + self.failure_retry_seconds
        logger.warning(
            "robots.txt erisilemedi (%s) — %ss boyunca fail-closed",
            redact_url(robots_url),
            int(self.failure_retry_seconds),
        )
        return parser

    async def is_allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        blocked_until = self._blocked_until.get(origin)
        if blocked_until is not None:
            if time.monotonic() < blocked_until:
                return False
            # Süre doldu: yeniden dene.
            self._blocked_until.pop(origin, None)
            self._parsers.pop(origin, None)
        if origin not in self._parsers:
            self._parsers[origin] = await self._fetch_parser(origin)
        parser = self._parsers[origin]
        return parser.can_fetch(self.user_agent, url)


class HttpClient:
    def __init__(
        self,
        user_agent: str = DEFAULT_UA,
        timeout: float = 15.0,
        enable_ssrf_guard: bool = True,
        limiter: HostLimiter | None = None,
        use_host_limiter: bool | None = None,
    ):
        self.user_agent = user_agent
        self.timeout = timeout
        self.enable_ssrf_guard = enable_ssrf_guard
        self.robots_cache = RobotsCache(user_agent)
        #: Paylaşılan client (R-005): bağlantı havuzu korunur. Client event loop'a
        #: bağlıdır; loop değişirse (Celery `asyncio.run`, test) yenisi kurulur.
        self._http_client: httpx.AsyncClient | None = None
        self._http_client_loop: object | None = None
        # Host bazli limiter: paralel gorevlerde ayni host'a yigilmayi engeller.
        # Varsayilan acik; SHOWIS_HOST_LIMITER=0 ile kapatilir.
        if limiter is not None:
            self.limiter: HostLimiter | None = limiter
        else:
            enabled = limiter_enabled() if use_host_limiter is None else use_host_limiter
            self.limiter = default_host_limiter() if enabled else None

    def _client(self) -> httpx.AsyncClient:
        """Süreç/loop başına paylaşılan client (guard açıksa DNS-pinli)."""
        try:
            loop: object | None = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover - loop dışı çağrı
            loop = None
        if self._http_client is None or self._http_client_loop is not loop:
            # Eski loop'un client'ı kapatılamaz (await gerekir); referans bırakılır.
            self._http_client = self._build_client()
            self._http_client_loop = loop
        return self._http_client

    def _build_client(self) -> httpx.AsyncClient:
        if self.enable_ssrf_guard:
            return httpx.AsyncClient(
                timeout=self.timeout, transport=pinned_async_transport()
            )
        return httpx.AsyncClient(timeout=self.timeout)

    async def aclose(self) -> None:
        """Paylaşılan client'ı kapat (servis kapanışı / test temizliği)."""
        client = self._http_client
        self._http_client = None
        self._http_client_loop = None
        if client is not None:
            await client.aclose()

    async def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        check_robots: bool = True,
        max_retries: int = 3,
        **kwargs,
    ) -> httpx.Response:
        # Yonlendirmeleri elle izliyoruz: httpx'in follow_redirects'i her adimi
        # SSRF guard ve robots kontrolunden gecirmeden takip eder.
        follow = kwargs.pop("follow_redirects", False)

        merged_headers = {"User-Agent": self.user_agent}
        if headers:
            merged_headers.update(headers)

        last_error = None
        last_response: httpx.Response | None = None
        for attempt in range(max_retries):
            try:
                client = self._client()
                current = url
                for _hop in range(MAX_REDIRECTS + 1):
                    if self.enable_ssrf_guard:
                        # guard_url DNS cozumu bloklayicidir; loop'u bogmamak icin thread'e al
                        await asyncio.to_thread(guard_url, current)
                    if check_robots and not await self.robots_cache.is_allowed(current):
                        raise PermissionError(
                            f"robots.txt uyarınca erişim engellendi: {current}"
                        )

                    _consume_request_budget()
                    _record_request()
                    if self.limiter is not None:
                        async with self.limiter.slot(current):
                            resp = await client.request(
                                method, current, headers=merged_headers, **kwargs
                            )
                    else:
                        resp = await client.request(
                            method, current, headers=merged_headers, **kwargs
                        )

                    location = resp.headers.get("location")
                    if not (follow and resp.is_redirect and location):
                        break
                    current = str(resp.url.join(location))
                    logger.info(
                        "Yonlendirme izleniyor: %s -> %s",
                        redact_url(url),
                        redact_url(current),
                    )
                else:
                    raise RuntimeError(
                        f"Cok fazla yonlendirme ({MAX_REDIRECTS}): {redact_url(url)}"
                    )

                # Handle GitHub and generic rate limits (tek denemede retry anlamsiz)
                if attempt + 1 < max_retries and (
                    resp.status_code == 429
                    or (resp.status_code == 403 and "rate limit" in resp.text.lower())
                ):
                    retry_after = resp.headers.get("Retry-After")
                    wait_sec = (
                        float(retry_after)
                        if retry_after
                        else (2**attempt + random.uniform(0.1, 0.5))
                    )
                    logger.warning(
                        "Hız limiti aşıldı (%s), %s saniye bekleniyor...",
                        redact_url(url),
                        wait_sec,
                    )
                    await asyncio.sleep(min(wait_sec, 60.0))
                    continue

                if resp.status_code >= 500:
                    # Son denemede yanıtı çağırana döndür: 5xx'i sınıflayan
                    # katmanlar (sitecheck absent_statuses, recon) karar verebilsin.
                    last_response = resp
                    await asyncio.sleep(1.0 + random.uniform(0.1, 0.3))
                    continue

                return resp

            except (PermissionError, SSRFViolationError, BudgetExceededError):
                # Fail-closed: butce asimi/robot/SSRF tekrar denenmez, istek atilmaz
                raise
            except Exception as e:
                last_error = e
                await asyncio.sleep(1.0)

        if last_response is not None:
            return last_response

        # Hata metni URL/anahtar tasiyabilir: redakte edilerek yukseltilir
        raise RuntimeError(
            f"HTTP isteği başarısız oldu ({max_retries} deneme): "
            f"{_redact_error(last_error)}"
        )

    async def get(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("POST", url, **kwargs)

    async def get_limited(
        self,
        url: str,
        *,
        max_bytes: int,
        headers: dict[str, str] | None = None,
        check_robots: bool = True,
        follow_redirects: bool = False,
        max_redirects: int = MAX_REDIRECTS,
    ) -> httpx.Response:
        """GET; gövde `max_bytes` ile **okuma sırasında** sınırlanır (P-009).

        `HttpClient.get` yanıtı tamamen belleğe alır; saldırgan kontrollü bir
        URL (avatar, profil sayfası) büyük gövde döndürerek worker belleğini
        tüketebilirdi. Bu varyant `client.stream` kullanır, sınırı aşınca
        bağlantıyı bırakır ve kısaltılmış içerikle sentetik bir `Response`
        döndürür (`extensions["showis_truncated"]`).

        Yönlendirmeler guard + robots kapısından geçer; tek deneme yapılır
        (retry çağıranın sorumluluğunda). `Content-Length`/`Content-Encoding`
        başlıkları kırpılmış gövdeyle tutarsız olacağı için kaldırılır.
        """
        if max_bytes <= 0:
            raise ValueError("max_bytes pozitif olmali")
        merged_headers = {"User-Agent": self.user_agent}
        if headers:
            merged_headers.update(headers)
        client = self._client()
        current = url
        for _hop in range(max_redirects + 1):
            if self.enable_ssrf_guard:
                await asyncio.to_thread(guard_url, current)
            if check_robots and not await self.robots_cache.is_allowed(current):
                raise PermissionError(
                    f"robots.txt uyarınca erişim engellendi: {current}"
                )
            _consume_request_budget()
            _record_request()
            if self.limiter is not None:
                async with self.limiter.slot(current):
                    redirect_to, response = await self._stream_capped(
                        client,
                        current,
                        merged_headers,
                        max_bytes,
                        follow_redirects=follow_redirects,
                    )
            else:
                redirect_to, response = await self._stream_capped(
                    client,
                    current,
                    merged_headers,
                    max_bytes,
                    follow_redirects=follow_redirects,
                )
            if redirect_to is not None:
                current = redirect_to
                logger.info(
                    "Yonlendirme izleniyor: %s -> %s",
                    redact_url(url),
                    redact_url(current),
                )
                continue
            assert response is not None
            return response
        raise RuntimeError(
            f"Cok fazla yonlendirme ({max_redirects}): {redact_url(url)}"
        )

    async def _stream_capped(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: dict[str, str],
        max_bytes: int,
        *,
        follow_redirects: bool,
    ) -> tuple[str | None, httpx.Response | None]:
        """Tek hop: gövdeyi `max_bytes` ile okur; yönlendirme varsa hedefi döndürür."""
        async with client.stream("GET", url, headers=headers) as resp:
            location = resp.headers.get("location")
            if follow_redirects and resp.is_redirect and location:
                return str(resp.url.join(location)), None
            chunks: list[bytes] = []
            total = 0
            truncated = False
            async for chunk in resp.aiter_bytes():
                remaining = max_bytes - total
                if len(chunk) > remaining:
                    chunks.append(chunk[:remaining])
                    total = max_bytes
                    truncated = True
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total >= max_bytes:
                    truncated = True
                    break
            safe_headers = {
                key: value
                for key, value in resp.headers.items()
                if key.lower() not in ("content-length", "content-encoding")
            }
            response = httpx.Response(
                status_code=resp.status_code,
                headers=safe_headers,
                content=b"".join(chunks),
                request=resp.request,
                extensions={**resp.extensions, "showis_truncated": truncated},
            )
            return None, response


class EvidenceStore:
    def __init__(self, storage_dir: str | None = None):
        self.storage_dir = storage_dir or os.getenv("SHOWIS_EVIDENCE_DIR") or None

    def store_bytes(
        self,
        data: bytes,
        source_url: str,
        kind: str,
        http_status: int | None = 200,
        user_agent: str = DEFAULT_UA,
    ) -> Evidence:
        """Veriyi hash'ler ve Evidence modeli döndürür."""
        content_hash = hashlib.sha256(data).hexdigest()
        evidence_id = str(uuid.uuid4())
        storage_key = f"evidence/{evidence_id[:8]}/{content_hash[:16]}.{kind}"

        # storage_dir verilirse icerigi diske yaz (MinIO entegrasyonu ayri katmanda)
        if self.storage_dir:
            try:
                dest = Path(self.storage_dir) / storage_key
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
            except OSError as exc:
                logger.warning("Evidence yazilamadi (%s): %s", storage_key, exc)

        return Evidence(
            id=evidence_id,
            source_url=source_url,
            kind=kind,
            content_hash=content_hash,
            storage_key=storage_key,
            http_status=http_status,
            user_agent=user_agent,
            fetched_at=datetime.now(timezone.utc),
            truncated=False,
        )
