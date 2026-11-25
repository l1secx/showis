"""Domain pivotları: crt.sh, DoH, RDAP, Wayback CDX (respx ile mock, ağ yok)."""

from __future__ import annotations

import httpx
import pytest
import respx
from showis.db.models import Case, PoolItem
from showis.services.pool import create_item
from showis.services.reasoning import pivots as pivots_mod
from showis.services.reasoning.pivots import PivotError, pivots_for, run_pivot
from showis_connectors.domain_intel import MAX_WAYBACK_ROWS
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture
async def db_factory():
    from showis.db.models import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def no_ssrf_dns(monkeypatch):
    """Testte gerçek DNS çözümlemesi olmasın; politika ayrı testte doğrulanır."""
    monkeypatch.setattr(
        pivots_mod,
        "_domain_http_client",
        lambda: pivots_mod.HttpClient(timeout=5.0, enable_ssrf_guard=False),
    )


async def _domain_item(factory, value: str = "example.com"):
    async with factory() as db:
        case = Case(
            id="case-domain",
            name="Domain pivot testi",
            purpose="Herkese açık alan adı istihbaratı",
            legal_basis="public_sources",
        )
        db.add(case)
        await db.flush()
        item, _ = await create_item(
            db,
            case_id=case.id,
            kind="domain",
            label=value,
            value=value,
            source="ai",
            status="proposed",
        )
        await db.commit()
        return case.id, item.id


def test_pivots_for_domain_lists_new_transforms():
    ids = {pivot.id for pivot in pivots_for("domain")}
    assert {
        "domain_site",
        "domain_subdomains",
        "domain_dns",
        "domain_rdap",
        "domain_archive",
    } <= ids
    for pivot in pivots_for("domain"):
        if pivot.id != "domain_site":
            assert pivot.network is True


@pytest.mark.asyncio
async def test_subdomains_dedupe_wildcard_drop(db_factory):
    case_id, item_id = await _domain_item(db_factory)
    with respx.mock(assert_all_called=True) as router:
        router.get("https://crt.sh/", params={"q": "%.example.com", "output": "json"}).respond(
            200,
            json=[
                {"name_value": "www.example.com\n*.example.com"},
                {"name_value": "API.example.com"},
                {"name_value": "www.example.com"},
                {"name_value": "evil.test"},
            ],
        )
        async with db_factory() as db:
            case = await db.get(Case, case_id)
            item = await db.get(PoolItem, item_id)
            result = await run_pivot(
                db, case=case, item=item, pivot_id="domain_subdomains"
            )
    values = {row["value"] for row in result.items}
    assert values == {"www.example.com", "api.example.com"}
    assert all(row["status"] == "proposed" for row in result.items)
    assert result.relations and all(r["type"] == "linked_from" for r in result.relations)
    async with db_factory() as db:
        created = (await db.execute(select(PoolItem).where(PoolItem.source == "scan"))).scalars()
        rows = list(created)
    assert rows and all(row.status == "proposed" for row in rows)
    assert all(row.provenance.get("pivot") == "domain_subdomains" for row in rows)
    assert all(row.provenance.get("from_item") == item_id for row in rows)


@pytest.mark.asyncio
async def test_dns_records_and_spf(db_factory):
    case_id, item_id = await _domain_item(db_factory)
    answers = {
        "A": [{"type": 1, "data": "192.0.2.10"}],
        "AAAA": [{"type": 28, "data": "2001:db8::1"}],
        "MX": [{"type": 15, "data": "10 mail.example.com."}],
        "NS": [{"type": 2, "data": "ns1.example.com."}, {"type": 2, "data": "ns2.example.com."}],
        "TXT": [
            {"type": 16, "data": '"v=spf1 include:_spf.example.com ~all"'},
            {"type": 16, "data": '"google-site-verification=abc123"'},
        ],
    }
    with respx.mock(assert_all_called=True) as router:
        for rtype, rows in answers.items():
            router.get(
                "https://dns.google/resolve",
                params={"name": "example.com", "type": rtype},
            ).respond(200, json={"Status": 0, "Answer": rows})
        async with db_factory() as db:
            case = await db.get(Case, case_id)
            item = await db.get(PoolItem, item_id)
            result = await run_pivot(db, case=case, item=item, pivot_id="domain_dns")
            await db.refresh(item)
    kinds = [(row["kind"], row["value"]) for row in result.items]
    assert ("ip", "192.0.2.10") in kinds
    assert ("ip", "2001:db8::1") in kinds
    assert ("domain", "mail.example.com") in kinds
    assert ("domain", "ns1.example.com") in kinds
    intel = item.attributes["domain_intel"]["dns"]
    assert intel["spf"] == ["v=spf1 include:_spf.example.com ~all"]
    assert intel["verification"] == ["google-site-verification=abc123"]
    assert intel["source"] == "dns.google"


@pytest.mark.asyncio
async def test_dns_nxdomain_reports_note(db_factory):
    case_id, item_id = await _domain_item(db_factory, "nxdomain.example")
    with respx.mock(assert_all_called=True) as router:
        for rtype in ("A", "AAAA", "MX", "NS", "TXT"):
            router.get(
                "https://dns.google/resolve",
                params={"name": "nxdomain.example", "type": rtype},
            ).respond(200, json={"Status": 3})
        async with db_factory() as db:
            case = await db.get(Case, case_id)
            item = await db.get(PoolItem, item_id)
            result = await run_pivot(db, case=case, item=item, pivot_id="domain_dns")
    assert result.items == []
    assert any("kayıt bulunamadı" in note for note in result.notes)


@pytest.mark.asyncio
async def test_rdap_keeps_redacted_registrar(db_factory):
    case_id, item_id = await _domain_item(db_factory)
    with respx.mock(assert_all_called=True) as router:
        router.get("https://rdap.org/domain/example.com").respond(
            200,
            json={
                "handle": "2336799_DOMAIN_COM-VRSN",
                "events": [
                    {"eventAction": "registration", "eventDate": "1995-08-14T04:00:00Z"},
                    {"eventAction": "expiration", "eventDate": "2026-08-13T04:00:00Z"},
                ],
                "nameservers": [{"ldhName": "A.IANA-SERVERS.NET"}],
                "entities": [
                    {
                        "roles": ["registrar"],
                        "vcardArray": ["vcard", [["fn", {}, "text", "REDACTED FOR PRIVACY"]]],
                    }
                ],
                "status": ["client delete prohibited"],
            },
        )
        async with db_factory() as db:
            case = await db.get(Case, case_id)
            item = await db.get(PoolItem, item_id)
            result = await run_pivot(db, case=case, item=item, pivot_id="domain_rdap")
            await db.refresh(item)
    record = item.attributes["domain_intel"]["rdap"]
    assert record["registrar"] == "REDACTED FOR PRIVACY"
    assert record["created_at"] == "1995-08-14T04:00:00Z"
    assert record["expires_at"] == "2026-08-13T04:00:00Z"
    assert record["nameservers"] == ["a.iana-servers.net"]
    assert ("domain", "a.iana-servers.net") in {
        (row["kind"], row["value"]) for row in result.items
    }
    assert any("registrar" in note for note in result.notes)


@pytest.mark.asyncio
async def test_rdap_missing_domain_is_pivot_error(db_factory):
    case_id, item_id = await _domain_item(db_factory)
    with respx.mock(assert_all_called=True) as router:
        router.get("https://rdap.org/domain/example.com").respond(404)
        async with db_factory() as db:
            case = await db.get(Case, case_id)
            item = await db.get(PoolItem, item_id)
            with pytest.raises(PivotError, match="RDAP"):
                await run_pivot(db, case=case, item=item, pivot_id="domain_rdap")


@pytest.mark.asyncio
async def test_wayback_first_last_and_samples(db_factory):
    case_id, item_id = await _domain_item(db_factory)
    with respx.mock(assert_all_called=True) as router:
        router.get(
            "https://web.archive.org/cdx/search/cdx",
            params={
                "url": "example.com/*",
                "output": "json",
                "limit": str(MAX_WAYBACK_ROWS),
                "collapse": "urlkey",
                "fl": "timestamp,original",
            },
        ).respond(
            200,
            json=[
                ["timestamp", "original"],
                ["20150101120000", "http://example.com/"],
                ["20200102030405", "https://example.com/about"],
                ["20200102030405", "https://example.com/about"],
            ],
        )
        async with db_factory() as db:
            case = await db.get(Case, case_id)
            item = await db.get(PoolItem, item_id)
            result = await run_pivot(db, case=case, item=item, pivot_id="domain_archive")
            await db.refresh(item)
    history = item.attributes["domain_intel"]["wayback"]
    assert history["count"] == 3
    assert history["first_seen"] == "2015-01-01T12:00:00Z"
    assert history["last_seen"] == "2020-01-02T03:04:05Z"
    urls = {row["value"] for row in result.items}
    assert "http://example.com/" in urls
    assert "https://example.com/about" in urls


@pytest.mark.asyncio
async def test_http_error_becomes_readable_pivot_error(db_factory):
    case_id, item_id = await _domain_item(db_factory)
    with respx.mock(assert_all_called=True) as router:
        router.get("https://crt.sh/").respond(403)
        async with db_factory() as db:
            case = await db.get(Case, case_id)
            item = await db.get(PoolItem, item_id)
            with pytest.raises(PivotError, match="crt.sh.*HTTP 403"):
                await run_pivot(db, case=case, item=item, pivot_id="domain_subdomains")


@pytest.mark.asyncio
async def test_response_size_cap(db_factory):
    case_id, item_id = await _domain_item(db_factory)
    with respx.mock(assert_all_called=True) as router:
        router.get("https://rdap.org/domain/example.com").respond(
            200,
            headers={"content-length": "999999999"},
            json={"events": []},
        )
        async with db_factory() as db:
            case = await db.get(Case, case_id)
            item = await db.get(PoolItem, item_id)
            with pytest.raises(PivotError, match="sınırı aşıldı"):
                await run_pivot(db, case=case, item=item, pivot_id="domain_rdap")


@pytest.mark.asyncio
async def test_timeout_becomes_pivot_error(db_factory):
    case_id, item_id = await _domain_item(db_factory)
    with respx.mock(assert_all_called=True) as router:
        router.get("https://crt.sh/").mock(side_effect=httpx.ConnectTimeout("timeout"))
        async with db_factory() as db:
            case = await db.get(Case, case_id)
            item = await db.get(PoolItem, item_id)
            with pytest.raises(PivotError, match="crt.sh"):
                await run_pivot(db, case=case, item=item, pivot_id="domain_subdomains")


@pytest.mark.asyncio
async def test_invalid_domain_rejected_without_network(db_factory):
    case_id, item_id = await _domain_item(db_factory, "not_a_domain")
    with respx.mock(assert_all_called=False):
        async with db_factory() as db:
            case = await db.get(Case, case_id)
            item = await db.get(PoolItem, item_id)
            with pytest.raises(PivotError, match="alan adı"):
                await run_pivot(db, case=case, item=item, pivot_id="domain_subdomains")
