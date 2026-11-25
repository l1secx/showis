"""domain_intel connector birim testleri (respx; gerçek ağ yok)."""

from __future__ import annotations

import pytest
import respx
from showis_connectors.domain_intel import (
    DomainIntelError,
    fetch_dns_records,
    fetch_rdap_record,
    fetch_subdomains,
    fetch_wayback_history,
    normalize_domain,
)
from showis_engine.http import HttpClient


def _client() -> HttpClient:
    return HttpClient(timeout=5.0, enable_ssrf_guard=False)


def test_normalize_domain_strips_scheme_and_www():
    assert normalize_domain("HTTPS://WWW.Example.COM/path?q=1") == "example.com"
    assert normalize_domain("example.com.") == "example.com"


@pytest.mark.parametrize("value", ["", "localhost", "not a domain", "exa_mple.com"])
def test_normalize_domain_rejects_invalid(value):
    with pytest.raises(DomainIntelError):
        normalize_domain(value)


@pytest.mark.asyncio
async def test_subdomains_capped_deduped_wildcard_dropped():
    payload = [{"name_value": f"h{i}.example.com\n*.h{i}.example.com"} for i in range(10)]
    with respx.mock(assert_all_called=True) as router:
        router.get("https://crt.sh/").respond(200, json=payload)
        names = await fetch_subdomains(_client(), "example.com", limit=4)
    assert names == sorted(names)
    assert len(names) == 4
    assert all(not name.startswith("*.") for name in names)


@pytest.mark.asyncio
async def test_dns_txt_split_quotes_joined():
    with respx.mock(assert_all_called=True) as router:
        for rtype in ("A", "AAAA", "MX", "NS", "TXT"):
            router.get(
                "https://dns.google/resolve",
                params={"name": "example.com", "type": rtype},
            ).respond(
                200,
                json={
                    "Status": 0,
                    "Answer": (
                        [{"type": 16, "data": '"v=spf1 "-"include:mx.example.com ~all"'}]
                        if rtype == "TXT"
                        else []
                    ),
                },
            )
        records = await fetch_dns_records(_client(), "example.com")
    assert records.txt == ["v=spf1 include:mx.example.com ~all"]
    assert records.spf == records.txt


@pytest.mark.asyncio
async def test_dns_all_types_failing_raises():
    with respx.mock(assert_all_called=True) as router:
        router.get("https://dns.google/resolve").respond(500)
        with pytest.raises(DomainIntelError, match="DNS"):
            await fetch_dns_records(_client(), "example.com")


@pytest.mark.asyncio
async def test_rdap_requires_dict_payload():
    with respx.mock(assert_all_called=True) as router:
        router.get("https://rdap.org/domain/example.com").respond(200, json=["bozuk"])
        with pytest.raises(DomainIntelError, match="biçimi"):
            await fetch_rdap_record(_client(), "example.com")


@pytest.mark.asyncio
async def test_wayback_skips_header_and_bad_rows():
    with respx.mock(assert_all_called=True) as router:
        router.get("https://web.archive.org/cdx/search/cdx").respond(
            200,
            json=[
                ["timestamp", "original"],
                ["20220101000000", "https://example.com/x"],
                ["bozuk", "https://example.com/y"],
                ["20230101000000", "ftp://example.com/z"],
            ],
        )
        history = await fetch_wayback_history(_client(), "example.com")
    assert history["count"] == 2
    assert history["last_seen"] == "2023-01-01T00:00:00Z"
    assert [row["url"] for row in history["samples"]] == ["https://example.com/x"]
