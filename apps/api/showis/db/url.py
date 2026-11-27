"""`DATABASE_URL` normalizasyonu: yönetilen Postgres (Neon, Vercel) bağlantı dizelerini
SQLAlchemy asyncpg biçimine çevirir."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

#: asyncpg'nin DSN sorgusunda tanımadığı libpq parametreleri.
_DROPPED_PARAMS = {"channel_binding"}


def normalize_database_url(url: str) -> str:
    """`postgres://` / `postgresql://` -> `postgresql+asyncpg://`; `sslmode` -> `ssl`.

    Zaten `postgresql+asyncpg://` olan ve sorgusu uyumlu dizeler değişmeden döner.
    """
    parts = urlsplit(url)
    scheme = parts.scheme
    if scheme in ("postgres", "postgresql"):
        scheme = "postgresql+asyncpg"
    if not scheme.startswith("postgresql+asyncpg"):
        return url

    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key in _DROPPED_PARAMS:
            continue
        query.append(("ssl" if key == "sslmode" else key, value))
    return urlunsplit(parts._replace(scheme=scheme, query=urlencode(query)))
