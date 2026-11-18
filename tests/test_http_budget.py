"""Z4-3 — robots.txt istekleri de tarama butcesinden duser (I1).

Ag yok: `respx` ile robots + hedef yanitlari mock'lanir.
"""

from __future__ import annotations

import pytest
import respx
from showis_engine.http import (
    BudgetExceededError,
    HttpClient,
    current_request_budget,
    reset_request_budget,
    set_request_budget,
)


def _client() -> HttpClient:
    return HttpClient(
        enable_ssrf_guard=False, use_host_limiter=False, timeout=5.0
    )


async def test_robots_istegi_butceden_duser():
    client = _client()
    token = set_request_budget(10)
    try:
        with respx.mock(assert_all_called=False) as router:
            router.get("https://butce.example/robots.txt").respond(
                200, text="User-agent: *\nAllow: /\n"
            )
            router.get("https://butce.example/sayfa").respond(200, text="ok")
            resp = await client.get("https://butce.example/sayfa", check_robots=True)
        assert resp.status_code == 200
        assert current_request_budget().used == 2, "robots + sayfa = 2 istek"
    finally:
        reset_request_budget(token)


async def test_robots_cache_ikinci_istekte_sayilmaz():
    client = _client()
    token = set_request_budget(10)
    try:
        with respx.mock(assert_all_called=False) as router:
            robots = router.get("https://cache.example/robots.txt").respond(
                200, text="User-agent: *\nAllow: /\n"
            )
            router.get("https://cache.example/a").respond(200, text="ok")
            router.get("https://cache.example/b").respond(200, text="ok")
            await client.get("https://cache.example/a", check_robots=True)
            await client.get("https://cache.example/b", check_robots=True)
        assert robots.call_count == 1, "robots origin basina bir kez cekilir"
        assert current_request_budget().used == 3, "1 robots + 2 sayfa"
    finally:
        reset_request_budget(token)


async def test_robots_butcesi_bittiyse_istek_atilmaz():
    """Butce tukendiginde robots istegi de fail-closed davranir."""
    client = _client()
    token = set_request_budget(1)
    try:
        with respx.mock(assert_all_called=False) as router:
            router.get("https://son.example/robots.txt").respond(
                200, text="User-agent: *\nAllow: /\n"
            )
            router.get("https://son.example/x").respond(200, text="ok")
            # Ilk istek robots'u tuketir, sayfa icin butce kalmaz
            with pytest.raises(BudgetExceededError):
                await client.get("https://son.example/x", check_robots=True)
        assert current_request_budget().used == 1
    finally:
        reset_request_budget(token)


async def test_robots_kapaliyken_sayilmaz():
    client = _client()
    token = set_request_budget(10)
    try:
        with respx.mock(assert_all_called=False) as router:
            router.get("https://kt.example/x").respond(200, text="ok")
            await client.get("https://kt.example/x", check_robots=False)
        assert current_request_budget().used == 1
    finally:
        reset_request_budget(token)
