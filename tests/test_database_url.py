import pytest
from showis.db.url import normalize_database_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "postgresql://u:p@ep-x.neon.tech/db?sslmode=require&channel_binding=require",
            "postgresql+asyncpg://u:p@ep-x.neon.tech/db?ssl=require",
        ),
        ("postgres://u:p@h/db", "postgresql+asyncpg://u:p@h/db"),
        ("postgresql+asyncpg://u:p@h:5432/db", "postgresql+asyncpg://u:p@h:5432/db"),
        ("sqlite+aiosqlite:///:memory:", "sqlite+aiosqlite:///:memory:"),
    ],
)
def test_normalize_database_url(raw, expected):
    assert normalize_database_url(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("redis://redis:6379/0", "redis://redis:6379/0"),
        ("rediss://u:p@h:6379", "rediss://u:p@h:6379?ssl_cert_reqs=required"),
        ("rediss://u:p@h:6379/0?x=1", "rediss://u:p@h:6379/0?x=1&ssl_cert_reqs=required"),
        ("rediss://h/0?ssl_cert_reqs=none", "rediss://h/0?ssl_cert_reqs=none"),
    ],
)
def test_celery_redis_url(raw, expected):
    from showis.core.queues import celery_redis_url

    assert celery_redis_url(raw) == expected
