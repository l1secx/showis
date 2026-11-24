"""Anahtarsız sızıntı sağlayıcı zinciri ve Pwned Passwords k-anonimlik ucu."""

from __future__ import annotations

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient
from showis.db.base import Base
from showis.db.session import get_db
from showis.main import app
from showis.services import breach
from showis.services.breach import BreachError, check_email, hibp
from showis.services.breach.pwned_passwords import parse_range
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

XON_ANALYTICS = r"https://api\.xposedornot\.com/v1/breach-analytics.*"
XON_SIMPLE = r"https://api\.xposedornot\.com/v1/check-email/.*"
HUDSON = r"https://cavalier\.hudsonrock\.com/.*"
HIBP = r"https://haveibeenpwned\.com/api/v3/breachedaccount/.*"


@pytest.fixture
async def db_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield factory
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.fixture
async def client(db_factory):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def _no_hibp_key(monkeypatch):
    monkeypatch.delenv("HIBP_API_KEY", raising=False)


ANALYTICS_PAYLOAD = {
    "ExposedBreaches": {
        "breaches_details": [
            {
                "breach": "Acme",
                "domain": "acme.example",
                "xposed_date": "2021",
                "xposed_data": "Email addresses;Passwords",
                "xposed_records": 1234,
                "details": "<b>Acme</b> sızıntısı",
                "verified": "Yes",
            }
        ]
    }
}


@pytest.mark.asyncio
async def test_xon_analytics_and_hudson_summary_drops_sensitive(db_factory):
    hudson_payload = {
        "stealers": [
            {
                "date_compromised": "2023-05-01T00:00:00.000Z",
                "stealer_family": "RedLine",
                "operating_system": "Windows 10",
                "total_corporate_services": 2,
                "total_user_services": 7,
                "computer_name": "SECRET-PC",
                "ip": "203.0.113.9",
                "passwords": ["hunter2"],
                "top_logins": ["u***@example.com"],
            }
        ]
    }
    async with db_factory() as db:
        with respx.mock(assert_all_called=True) as mock:
            mock.get(url__regex=XON_ANALYTICS).mock(
                return_value=httpx.Response(200, json=ANALYTICS_PAYLOAD)
            )
            mock.get(url__regex=HUDSON).mock(
                return_value=httpx.Response(200, json=hudson_payload)
            )
            report = await check_email(db, "Kisi@Example.com")

    assert report["email"] == "kisi@example.com"
    assert report["breached"] is True
    assert report["password_exposed"] is True
    assert report["providers"] == ["xposedornot", "hudsonrock"]
    assert report["breaches"][0]["name"] == "Acme"
    assert "<b>" not in report["breaches"][0]["description"]
    stealer = report["infostealer"]["stealers"][0]
    assert stealer["stealer_family"] == "RedLine"
    dumped = repr(report)
    for secret in ("SECRET-PC", "203.0.113.9", "hunter2", "u***@example.com"):
        assert secret not in dumped


@pytest.mark.asyncio
async def test_xon_falls_back_to_simple_endpoint(db_factory):
    async with db_factory() as db:
        with respx.mock(assert_all_called=True) as mock:
            mock.get(url__regex=XON_ANALYTICS).mock(return_value=httpx.Response(500))
            mock.get(url__regex=XON_SIMPLE).mock(
                return_value=httpx.Response(200, json={"breaches": [["Foo", "Bar"]]})
            )
            mock.get(url__regex=HUDSON).mock(return_value=httpx.Response(404))
            report = await check_email(db, "a@example.com")
    assert [b["name"] for b in report["breaches"]] == ["Foo", "Bar"]
    assert report["infostealer"]["count"] == 0
    assert report["password_exposed"] is False


@pytest.mark.asyncio
async def test_hibp_only_used_with_key(db_factory, monkeypatch):
    async with db_factory() as db:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(url__regex=XON_ANALYTICS).mock(return_value=httpx.Response(404))
            mock.get(url__regex=HUDSON).mock(return_value=httpx.Response(404))
            hibp_route = mock.get(url__regex=HIBP).mock(return_value=httpx.Response(200, json=[]))
            report = await check_email(db, "a@example.com")
            assert not hibp_route.called
            assert "haveibeenpwned" not in report["providers"]

        monkeypatch.setenv("HIBP_API_KEY", "k")
        assert await hibp.api_key_from_settings(db) == "k"
        with respx.mock(assert_all_called=True) as mock:
            mock.get(url__regex=XON_ANALYTICS).mock(return_value=httpx.Response(404))
            mock.get(url__regex=HUDSON).mock(return_value=httpx.Response(404))
            mock.get(url__regex=HIBP).mock(return_value=httpx.Response(200, json=[]))
            report = await check_email(db, "a@example.com")
        assert "haveibeenpwned" in report["providers"]


@pytest.mark.asyncio
async def test_all_providers_failing_raises_breach_error(db_factory):
    async with db_factory() as db:
        with respx.mock() as mock:
            mock.get(url__regex=XON_ANALYTICS).mock(return_value=httpx.Response(500))
            mock.get(url__regex=XON_SIMPLE).mock(return_value=httpx.Response(500))
            mock.get(url__regex=HUDSON).mock(return_value=httpx.Response(429))
            with pytest.raises(BreachError):
                await check_email(db, "a@example.com")
    assert breach.BreachError is BreachError


@pytest.mark.asyncio
async def test_endpoint_returns_502_when_all_fail(db_factory, client):
    from showis.db.models import Case, PoolItem

    async with db_factory() as session:
        session.add(
            Case(
                id="c1", name="x", purpose="p", legal_basis="self_check",
                status="active", retention_days=30,
            )
        )
        await session.flush()
        item = PoolItem(
            case_id="c1", kind="email", label="e", value="a@example.com",
            normalized="a@example.com", source="user", status="confirmed",
            attributes={}, provenance={},
        )
        session.add(item)
        await session.commit()
        item_id = item.id
    with respx.mock() as mock:
        mock.get(url__regex=XON_ANALYTICS).mock(return_value=httpx.Response(500))
        mock.get(url__regex=XON_SIMPLE).mock(return_value=httpx.Response(500))
        mock.get(url__regex=HUDSON).mock(return_value=httpx.Response(500))
        response = await client.post(f"/api/v1/cases/c1/pool/{item_id}/breach-check")
    assert response.status_code == 502


def test_parse_range_drops_padding_and_junk():
    body = "0018A45C4D1DEF81644B54AB7F969B88D65:3\r\n" + "0" * 35 + ":0\r\nbad:1\r\n"
    assert parse_range(body) == [
        {"suffix": "0018A45C4D1DEF81644B54AB7F969B88D65", "count": 3}
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prefix", ["ABCD", "ABCDEF", "ZZZZZ", "5BAA61E4C9B93F3F0682250B6CF8331B7EE68FD8"]
)
async def test_password_range_rejects_bad_prefix(client, prefix):
    response = await client.get(f"/api/v1/breach/password-range/{prefix}")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_password_range_sends_only_five_chars(client):
    suffix = "1E4C9B93F3F0682250B6CF8331B7EE68FD8"
    with respx.mock() as mock:
        route = mock.get(url__regex=r"https://api\.pwnedpasswords\.com/range/[0-9A-F]{5}$").mock(
            return_value=httpx.Response(200, text=f"{suffix}:42\r\n{'0' * 35}:0\r\n")
        )
        response = await client.get("/api/v1/breach/password-range/5baa6")
    assert response.status_code == 200, response.text
    assert response.json() == {"prefix": "5BAA6", "suffixes": [{"suffix": suffix, "count": 42}]}
    request = route.calls.last.request
    assert request.url.path == "/range/5BAA6"
    assert request.headers["add-padding"] == "true"
    assert len(request.url.path.rsplit("/", 1)[1]) == 5
