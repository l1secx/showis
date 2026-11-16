import httpx
import pytest
import respx
from showis_engine.http import EvidenceStore, HttpClient


@pytest.mark.asyncio
async def test_http_client_custom_ua_and_retry():
    client = HttpClient(
        user_agent="Showis/0.1 (+https://github.com/showis-osint)", enable_ssrf_guard=False
    )

    with respx.mock:
        # First 429 then 200
        respx.get("https://api.github.com/test-endpoint").side_effect = [
            httpx.Response(429, headers={"Retry-After": "0.1"}),
            httpx.Response(200, json={"status": "ok"}),
        ]

        # robots allow all
        respx.get("https://api.github.com/robots.txt").respond(404)

        resp = await client.get("https://api.github.com/test-endpoint")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


def test_evidence_store():
    store = EvidenceStore()
    data = b'{"login": "testuser", "public_repos": 5}'
    ev = store.store_bytes(data, source_url="https://api.github.com/users/testuser", kind="json")

    assert ev.id is not None
    assert ev.kind == "json"
    import hashlib

    expected_hash = hashlib.sha256(data).hexdigest()
    assert ev.content_hash == expected_hash
    assert ev.source_url == "https://api.github.com/users/testuser"


@pytest.mark.asyncio
async def test_redirect_hop_is_revalidated():
    """Yonlendirme hedefi de SSRF kontrolunden gecmeli (sadece ilk hop degil)."""
    from showis_engine.policy import SSRFViolationError

    client = HttpClient(user_agent="Showis/0.1", enable_ssrf_guard=True)
    with respx.mock(assert_all_called=False) as router:
        router.get("https://example.com/robots.txt").respond(404)
        router.get("https://example.com/go").respond(
            302, headers={"Location": "http://169.254.169.254/latest/meta-data/"}
        )
        with pytest.raises(SSRFViolationError):
            await client.get("https://example.com/go", follow_redirects=True)
