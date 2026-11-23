"""Domain/IP recon connector testleri — ücretsiz RDAP/DNS/crt.sh/Wayback."""

from __future__ import annotations

import pytest
import respx
from showis_connectors import DomainReconConnector, IpReconConnector
from showis_contracts import EntityType, Platform, ScanContext, Target
from showis_engine.http import HttpClient


def _ctx(kind: str, **values) -> ScanContext:
    return ScanContext(
        case_id="c1",
        scan_id="s1",
        target=Target(kind=kind, **values),
    )


@pytest.mark.asyncio
async def test_domain_plan_only_for_domain_target():
    conn = DomainReconConnector(http_client=HttpClient(enable_ssrf_guard=False))
    assert await conn.plan(_ctx("name", first_name="Ada")) == []
    tasks = await conn.plan(_ctx("domain", domain="example.com"))
    assert len(tasks) == 1
    assert tasks[0].params["identifier"] == "example.com"


@pytest.mark.asyncio
async def test_domain_recon_normalizes_entity():
    conn = DomainReconConnector(http_client=HttpClient(enable_ssrf_guard=False))
    with respx.mock(assert_all_called=False) as router:
        router.get("https://rdap.org/domain/example.com").respond(
            200,
            json={
                "events": [
                    {"eventAction": "registration", "eventDate": "2010-01-01T00:00:00Z"}
                ],
                "nameservers": [{"ldhName": "NS1.EXAMPLE.COM"}],
                "entities": [
                    {"vcardArray": ["vcard", [["fn", {}, "text", "Example Registrar"]]]}
                ],
            },
        )
        for rtype, answers in {
            "A": [{"data": "192.0.2.10"}],
            "AAAA": [],
            "MX": [{"data": "mail.example.com."}],
            "NS": [{"data": "ns1.example.com."}],
        }.items():
            router.get(
                "https://dns.google/resolve", params={"name": "example.com", "type": rtype}
            ).respond(200, json={"Answer": answers})
        router.get(
            "https://crt.sh/", params={"q": "%.example.com", "output": "json"}
        ).respond(
            200,
            json=[
                {"name_value": "www.example.com\n*.example.com"},
                {"name_value": "api.example.com"},
            ],
        )
        router.get("https://web.archive.org/cdx/search/cdx").respond(
            200,
            json=[["timestamp", "original"], ["2015", "http://example.com/"]],
        )
        task = (await conn.plan(_ctx("domain", domain="example.com")))[0]
        obs = await conn.run(task, _ctx("domain", domain="example.com"))

    assert obs[0].data["exists"] is True
    entities, _ = conn.normalize(obs[0])
    assert len(entities) == 1
    entity = entities[0]
    assert entity.type == EntityType.DOMAIN
    assert entity.label == "example.com"
    assert entity.attributes["domain"] == "example.com"
    assert entity.attributes["registrar"] == "Example Registrar"
    assert "www.example.com" in entity.attributes["subdomains"]
    assert entity.attributes["dns"]["a"] == ["192.0.2.10"]
    assert entity.attributes["archive_samples"][0]["timestamp"] == "2015"


@pytest.mark.asyncio
async def test_ip_recon_normalizes_entity():
    conn = IpReconConnector(http_client=HttpClient(enable_ssrf_guard=False))
    with respx.mock(assert_all_called=False) as router:
        router.get("https://rdap.org/ip/192.0.2.1").respond(
            200,
            json={
                "name": "EXAMPLE-NET",
                "country": "US",
                "handle": "NET-192-0-2-0-1",
                "entities": [
                    {"vcardArray": ["vcard", [["fn", {}, "text", "Example Org"]]]}
                ],
            },
        )
        router.get(
            "https://dns.google/resolve",
            params={"name": "1.2.0.192.in-addr.arpa", "type": "PTR"},
        ).respond(200, json={"Answer": [{"data": "host.example.com."}]})
        task = (await conn.plan(_ctx("ip", ip="192.0.2.1")))[0]
        obs = await conn.run(task, _ctx("ip", ip="192.0.2.1"))

    assert obs[0].data["ptr"] == "host.example.com"
    entities, _ = conn.normalize(obs[0])
    entity = entities[0]
    assert entity.type == EntityType.IP
    assert entity.platform == Platform.WEB
    assert entity.attributes["ip"] == "192.0.2.1"
    assert entity.attributes["country"] == "US"
    assert entity.attributes["org"] == "Example Org"
