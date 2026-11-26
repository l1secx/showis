import ipaddress
import re
import socket
from urllib.parse import urlparse

from showis_contracts import RiskTier, Target, TaskSpec

FORBIDDEN_HOSTS = {
    "labs.hackthebox.com",
    "www.hackthebox.com/api",
    "pastebin.com",
}

FORBIDDEN_QUERY_PATTERNS = [
    re.compile(r"\b(password|leak|breach|dox|tckn|tc kimlik|kredi kartı)\b", re.IGNORECASE),
    re.compile(r"site:pastebin\.com", re.IGNORECASE),
    re.compile(r"site:anonfiles\.com", re.IGNORECASE),
]

UNDERAGE_HINTS_PATTERNS = [
    re.compile(r"\b(1[0-7]\s*yaşında|lise\s*öğrencisi|ortaokul|underage|minor)\b", re.IGNORECASE),
    # "11. sınıf", "9.sinif", "12 sınıf" gibi okul sınıfı ifadeleri (T-109/SEC-07)
    re.compile(r"\b(?:[1-9]|1[0-2])\s*\.?\s*s[ıi]n[ıi]f\b", re.IGNORECASE),
    # Okul adı ipucu lise/ortaokul ise hedef reşit değil varsayılır (AUP md.7)
    re.compile(r"\b(ilkokul|ortaokul|orta\s*okul|lise|lisesi)\b", re.IGNORECASE),
]


class PolicyViolationError(Exception):
    pass


class SSRFViolationError(PolicyViolationError):
    pass


#: CGNAT / Tailscale tailnet aralığı. Python `is_private` bunu kapsamaz;
#: `is_global` False olsa da açık reddetme savunma derinliği sağlar.
_CGNAT_NETWORKS = (ipaddress.ip_network("100.64.0.0/10"),)


def is_blocked_ip(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """SSRF için yasaklı IP sınıfları.

    Kural: **yalnız global (genel internete yönlendirilebilir) adresler** geçer.
    Böylece private/loopback/link-local/reserved/unspecified sınıflarına ek
    olarak CGNAT/tailnet (`100.64.0.0/10`) ve dokümantasyon TEST-NET aralıkları
    da reddedilir. IPv4-mapped IPv6 adresler (`::ffff:100.64.0.1`) IPv4 gibi
    değerlendirilir. Çoklu yayın (multicast) `is_global` True dönebildiği için
    ayrıca reddedilir.
    """
    mapped = getattr(ip_obj, "ipv4_mapped", None)
    if mapped is not None:
        ip_obj = mapped
    if not ip_obj.is_global:
        return True
    if ip_obj.is_multicast:
        return True
    if ip_obj.version == 4 and any(ip_obj in network for network in _CGNAT_NETWORKS):
        return True
    return False


def assert_host_not_forbidden(host_or_url: str) -> None:
    """Yasaklı host kontrolü (ör. labs.hackthebox.com, www.hackthebox.com/api)."""
    parsed = urlparse(host_or_url if "://" in host_or_url else f"http://{host_or_url}")
    host = (parsed.hostname or "").lower()
    path = parsed.path or "/"

    for forbidden in FORBIDDEN_HOSTS:
        if "/" in forbidden:
            # host + path öneki eşleşmesi (ör. www.hackthebox.com/api)
            fhost, fpath = forbidden.split("/", 1)
            if (host == fhost or host.endswith(f".{fhost}")) and path.startswith(
                f"/{fpath}"
            ):
                raise PolicyViolationError(f"Erişim yasaklı host/yol: {host}{path}")
        elif host == forbidden or host.endswith(f".{forbidden}"):
            raise PolicyViolationError(f"Erişim yasaklı host: {host}")

    if "hubspot.com" in host:
        raise PolicyViolationError(f"Yasaklı telemetri/izleyici host: {host}")


def guard_url(url: str) -> str:
    """SSRF Koruması: Sadece http/https; private/loopback/link-local IP'ler reddedilir."""
    if not url:
        raise SSRFViolationError("Boş URL kabul edilemez.")

    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https"):
        raise SSRFViolationError(f"Desteklenmeyen protokol: {parsed.scheme}")

    hostname = (parsed.hostname or "").lower()
    if not hostname or hostname in ("localhost", "127.0.0.1", "::1"):
        raise SSRFViolationError(f"Loopback adrese erişim engellendi: {hostname}")

    assert_host_not_forbidden(url)

    # IP çözümleme ve özel IP kontrolü
    try:
        # Doğrudan IP adresi kontrolü
        ip_obj = ipaddress.ip_address(hostname)
        if is_blocked_ip(ip_obj):
            raise SSRFViolationError(f"Özel/dahili IP adresine erişim engellendi: {hostname}")
    except ValueError:
        # Alan adı - DNS çözümü ile IP kontrolü
        try:
            addr_info = socket.getaddrinfo(hostname, None)
            for addr in addr_info:
                ip_str = addr[4][0]
                ip_obj = ipaddress.ip_address(ip_str)
                if is_blocked_ip(ip_obj):
                    raise SSRFViolationError(
                        f"Alan adı dahili IP'ye çözümlendi: {hostname} -> {ip_str}"
                    )
        except socket.gaierror:
            # DNS çözümlenemedi (mock ortamlarında pass edebilir). Bağlantı
            # anındaki pinli backend (`netguard`) hatayı fail-closed kapatır.
            pass

    return url


def guard_query(query: str) -> str:
    """Yasak desen kontrolü (leak, breach, password vb.)."""
    for pattern in FORBIDDEN_QUERY_PATTERNS:
        if pattern.search(query):
            raise PolicyViolationError(f"Yasaklı arama kalıbı tespit edildi: {query}")
    return query


def guard_target(target: Target) -> Target:
    """Reşit olmayan şüphe tespiti (ad + soyad + nick + hints)."""
    combined = (
        f"{target.first_name} {target.last_name} {target.nickname} "
        f"{target.identifier} {target.hints}"
    )
    for pattern in UNDERAGE_HINTS_PATTERNS:
        if pattern.search(combined):
            target.review_required = True
            break
    return target


def enforce_tier(task: TaskSpec, connector_tier: RiskTier) -> None:
    """Tier D yok; Tier C yalnızca arama indeksi türünde olabilir."""
    search_kinds = {"search", "search_result"}
    if connector_tier == RiskTier.C and task.kind not in search_kinds:
        raise PolicyViolationError(
            "Tier C kaynaklarına doğrudan profil/veri çekme isteği atılamaz."
        )
