"""T-358: vaka listesi — arama, filtre, sayfalama ve zarf sözleşmesi."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from showis.db.base import Base
from showis.db.models import Case as DbCase
from showis.db.models import Scan as DbScan
from showis.db.models import Target as DbTarget
from showis.db.session import get_db
from showis.main import app
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


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
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _seed(factory) -> None:
    """3 vaka: ad/soyad/purpose farklı + son tarama modları farklı."""
    base = datetime(2026, 11, 1, tzinfo=timezone.utc)
    async with factory() as session:
        session.add_all(
            [
                DbCase(
                    id="case-ayse",
                    name="Ayşe İncelemesi",
                    purpose="Kendini kontrol",
                    legal_basis="self_check",
                    status="active",
                    created_at=base,
                ),
                DbCase(
                    id="case-mehmet",
                    name="Mehmet Vakası",
                    purpose="İşe alım öncesi doğrulama",
                    legal_basis="consent",
                    status="active",
                    created_at=base + timedelta(days=1),
                ),
                DbCase(
                    id="case-review",
                    name="İnceleme Bekleyen",
                    purpose="Reşit olmayan şüphesi",
                    legal_basis="self_check",
                    status="review_pending",
                    created_at=base + timedelta(days=2),
                ),
            ]
        )
        session.add_all(
            [
                DbTarget(
                    case_id="case-ayse", first_name="Ayşe", last_name="Yılmaz", hints={}
                ),
                DbTarget(
                    case_id="case-mehmet", first_name="Mehmet", last_name="Demir", hints={}
                ),
                DbTarget(case_id="case-review", first_name="Anon", last_name="Yıldız", hints={}),
            ]
        )
        # Son tarama: ayse O3, mehmet O1 (created_at sırası önemli)
        session.add_all(
            [
                DbScan(
                    id="scan-ayse-1",
                    case_id="case-ayse",
                    mode="O2",
                    status="completed",
                    created_at=base,
                ),
                DbScan(
                    id="scan-ayse-2",
                    case_id="case-ayse",
                    mode="O3",
                    status="completed",
                    created_at=base + timedelta(hours=2),
                ),
                DbScan(
                    id="scan-mehmet",
                    case_id="case-mehmet",
                    mode="O1",
                    status="cancelled",
                    created_at=base + timedelta(days=1, hours=1),
                ),
            ]
        )
        await session.commit()


# ---------------------------------------------------------------------------
# Zarf + total
# ---------------------------------------------------------------------------


async def test_zarf_ve_total(client, db_factory):
    await _seed(db_factory)
    res = await client.get("/api/v1/cases")
    assert res.status_code == 200
    body = res.json()

    assert set(body) == {"items", "total", "limit", "offset"}
    assert body["total"] == 3
    assert body["limit"] == 50 and body["offset"] == 0
    assert len(body["items"]) == 3
    item = next(c for c in body["items"] if c["id"] == "case-ayse")
    assert item["target"] == {
        "kind": "name",
        "first_name": "Ayşe",
        "last_name": "Yılmaz",
        "nickname": "",
        "email": "",
        "phone": "",
        "domain": "",
        "ip": "",
    }
    assert item["last_scan"]["mode"] == "O3"  # en yeni tarama
    assert item["last_scan"]["status"] == "completed"


async def test_legacy_duz_liste(client, db_factory):
    await _seed(db_factory)
    res = await client.get("/api/v1/cases?legacy=1")
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body, list) and len(body) == 3
    # Eski alan kümesi korunur
    assert {"id", "name", "purpose", "legal_basis", "status", "retention_days"} <= set(body[0])


# ---------------------------------------------------------------------------
# Arama: ad, soyad, purpose + joker kaçışı
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query,expected",
    [
        ("ayşe", ["case-ayse"]),  # vaka adı + hedef adı
        ("yılmaz", ["case-ayse"]),  # hedef soyadı
        ("alım", ["case-mehmet"]),  # purpose (alt dizgi)
        ("MEHMET", ["case-mehmet"]),  # büyük/küçük harf duyarsız (ASCII katlama)
        ("yıldız", ["case-review"]),  # soyad (Türkçe küçük harf)
        ("yok-böyle", []),
    ],
)
async def test_arama_alanlari(client, db_factory, query, expected):
    await _seed(db_factory)
    res = await client.get(f"/api/v1/cases?q={query}")
    assert res.status_code == 200
    assert [c["id"] for c in res.json()["items"]] == expected


async def test_arama_nickname_handle_only(client, db_factory):
    """R18: handle-only vaka (ad/soyad NULL) `?q=` ile nick'ten bulunur."""
    await _seed(db_factory)
    async with db_factory() as session:
        session.add(
            DbCase(
                id="case-handle",
                name="Handle Vakası",
                purpose="kimlik biçimi testi",
                legal_basis="self_check",
            )
        )
        session.add(
            DbTarget(case_id="case-handle", kind="handle", nickname="kerem", hints={})
        )
        await session.commit()

    # nick eşleşmesi
    res = await client.get("/api/v1/cases?q=kerem")
    assert res.status_code == 200
    items = res.json()["items"]
    assert [c["id"] for c in items] == ["case-handle"]
    assert items[0]["target"]["nickname"] == "kerem"

    # eşleşmeyen nick dönmez
    res = await client.get("/api/v1/cases?q=baska")
    assert res.status_code == 200
    assert res.json()["items"] == []

    # ad/soyad araması bozulmadı
    res = await client.get("/api/v1/cases?q=yılmaz")
    assert [c["id"] for c in res.json()["items"]] == ["case-ayse"]


async def test_joker_kacisi(client, db_factory):
    """`%` ve `_` desen olarak YORUMLANMAZ (kullanıcı joker üretemez)."""
    await _seed(db_factory)
    async with db_factory() as session:
        session.add(
            DbCase(
                id="case-pct",
                name="Oran %50 incelemesi",
                purpose="test",
                legal_basis="self_check",
            )
        )
        session.add(
            DbTarget(case_id="case-pct", first_name="Yüzde", last_name="Elli", hints={})
        )
        await session.commit()

    # "%" tek başına hiçbir şeyi eşleştirmemeli (joker olsaydı tümü dönerdi)
    res = await client.get("/api/v1/cases?q=%25")  # URL-encoded %
    assert res.status_code == 200
    ids = [c["id"] for c in res.json()["items"]]
    assert ids == ["case-pct"]  # yalnız gerçek '%' içeren vaka

    # "_" joker olsaydı "case-ayse" gibi 4 harfli adlar eşleşirdi
    res = await client.get("/api/v1/cases?q=_")
    assert res.json()["items"] == []


# ---------------------------------------------------------------------------
# Filtreler
# ---------------------------------------------------------------------------


async def test_status_filtresi(client, db_factory):
    await _seed(db_factory)
    res = await client.get("/api/v1/cases?status=review_pending")
    assert res.status_code == 200
    assert [c["id"] for c in res.json()["items"]] == ["case-review"]
    assert res.json()["total"] == 1


async def test_gecersiz_status_422(client, db_factory):
    await _seed(db_factory)
    res = await client.get("/api/v1/cases?status=uydurma")
    assert res.status_code == 422


async def test_mode_filtresi_son_taramaya_bakar(client, db_factory):
    await _seed(db_factory)
    res = await client.get("/api/v1/cases?mode=O3")
    assert res.status_code == 200
    # case-ayse'nin SON taraması O3 (eski O2 değil)
    assert [c["id"] for c in res.json()["items"]] == ["case-ayse"]

    res = await client.get("/api/v1/cases?mode=o1")  # küçük harf kabul
    assert [c["id"] for c in res.json()["items"]] == ["case-mehmet"]


async def test_gecersiz_mode_422(client, db_factory):
    await _seed(db_factory)
    res = await client.get("/api/v1/cases?mode=O9")
    assert res.status_code == 422


async def test_arama_ve_filtre_birlikte(client, db_factory):
    await _seed(db_factory)
    res = await client.get("/api/v1/cases?q=bekleyen&status=review_pending")
    assert [c["id"] for c in res.json()["items"]] == ["case-review"]
    res = await client.get("/api/v1/cases?q=ayşe&status=active")
    assert [c["id"] for c in res.json()["items"]] == ["case-ayse"]


# ---------------------------------------------------------------------------
# Sayfalama + sıralama
# ---------------------------------------------------------------------------


async def test_limit_offset(client, db_factory):
    await _seed(db_factory)
    first = await client.get("/api/v1/cases?limit=2&offset=0")
    second = await client.get("/api/v1/cases?limit=2&offset=2")

    assert first.json()["total"] == second.json()["total"] == 3
    assert len(first.json()["items"]) == 2
    assert len(second.json()["items"]) == 1
    ids = {c["id"] for c in first.json()["items"]} | {
        c["id"] for c in second.json()["items"]
    }
    assert len(ids) == 3  # sayfalar arasında çakışma yok


@pytest.mark.parametrize("query", ["limit=0", "limit=201", "limit=-1", "offset=-1"])
async def test_limit_offset_sinirlari_422(client, db_factory, query):
    await _seed(db_factory)
    res = await client.get(f"/api/v1/cases?{query}")
    assert res.status_code == 422


async def test_gecersiz_sort_422(client, db_factory):
    await _seed(db_factory)
    res = await client.get("/api/v1/cases?sort=alakasiz")
    assert res.status_code == 422


@pytest.mark.parametrize(
    "sort,expected_first",
    [
        ("created_desc", "case-review"),
        ("created_asc", "case-ayse"),
        ("name_asc", "case-ayse"),  # "Ayşe İncelemesi"
        ("updated_desc", "case-review"),  # en yeni tarama case-mehmet'te ama vaka zamanı eski
    ],
)
async def test_sort(client, db_factory, sort, expected_first):
    await _seed(db_factory)
    res = await client.get(f"/api/v1/cases?sort={sort}")
    assert res.status_code == 200
    items = res.json()["items"]
    assert items[0]["id"] == expected_first


# ---------------------------------------------------------------------------
# N+1 kontrolü
# ---------------------------------------------------------------------------


async def test_n_plus_bir_yok(client, db_factory):
    """10 vaka + hedef + tarama: sorgu sayısı sabit kalmalı (N ile artmamalı)."""
    base = datetime(2026, 10, 1, tzinfo=timezone.utc)
    async with db_factory() as session:
        for index in range(10):
            case_id = f"case-n{index}"
            session.add(
                DbCase(
                    id=case_id,
                    name=f"Vaka {index}",
                    purpose="test",
                    legal_basis="self_check",
                    created_at=base + timedelta(days=index),
                )
            )
            session.add(
                DbTarget(
                    case_id=case_id,
                    first_name=f"Ad{index}",
                    last_name=f"Soyad{index}",
                    hints={},
                )
            )
            session.add(
                DbScan(
                    id=f"scan-n{index}",
                    case_id=case_id,
                    mode="O2",
                    status="completed",
                    created_at=base + timedelta(days=index),
                )
            )
        await session.commit()

    statements: list[str] = []

    def _before_execute(conn, cursor, statement, *args):  # noqa: ANN001
        statements.append(statement)

    engine = db_factory.kw["bind"]
    event.listen(engine.sync_engine, "before_cursor_execute", _before_execute)
    try:
        res = await client.get("/api/v1/cases?limit=10")
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _before_execute)

    assert res.status_code == 200
    assert len(res.json()["items"]) == 10
    # count + sayfa + targets + seeds + son tarama = 5 sorgu (N'den bağımsız)
    assert len(statements) <= 6, f"beklenen <=6 sorgu, ölçülen {len(statements)}: {statements}"


# ---------------------------------------------------------------------------
# Türkçe harf katlaması (İ/ı/ş/ğ/ü/ö/ç) — SQLite ve Postgres'te aynı
# ---------------------------------------------------------------------------


async def test_turkce_harf_katlamasi(client, db_factory):
    """Büyük/küçük Türkçe harf ve ASCII karşılıkları aynı sonucu verir."""
    await _seed(db_factory)
    # Alt dizgi araması (katlama gerekmez) çalışır — "Ayşe" ve "İşe alım"
    hit = {c["id"] for c in (await client.get("/api/v1/cases?q=şe")).json()["items"]}
    assert hit == {"case-ayse", "case-mehmet"}

    # Küçük `inceleme`, büyük `İnceleme` içeren adlarla artık eşleşir.
    expected = {"case-ayse", "case-review"}
    for query in ("inceleme", "İnceleme", "INCELEME"):
        response = await client.get("/api/v1/cases", params={"q": query})
        hit_ids = {c["id"] for c in response.json()["items"]}
        assert hit_ids == expected, query

    # ASCII karşılığı yazılan `ayse`, `Ayşe` hedefini bulur.
    hit_ids = {c["id"] for c in (await client.get("/api/v1/cases?q=ayse")).json()["items"]}
    assert hit_ids == {"case-ayse"}


# ---------------------------------------------------------------------------
# Çok kelimeli arama: kelimeler farklı alanlarda olabilir (AND)
# ---------------------------------------------------------------------------


async def test_cok_kelimeli_arama_alanlar_arasi(client, db_factory):
    """`ad soyad` ayrı kolonlarda olsa da tek aramada bulunur."""
    await _seed(db_factory)

    res = await client.get("/api/v1/cases", params={"q": "ayşe yılmaz"})
    assert [c["id"] for c in res.json()["items"]] == ["case-ayse"]

    # İki kelime farklı vakalara aitse eşleşme olmaz (OR değil, AND).
    res = await client.get("/api/v1/cases", params={"q": "ayşe demir"})
    assert res.json()["items"] == []

    # Kelimeler vaka adı + amaç arasında da eşleşebilir.
    res = await client.get("/api/v1/cases", params={"q": "mehmet doğrulama"})
    assert [c["id"] for c in res.json()["items"]] == ["case-mehmet"]


async def test_ilgi_sirasi(client, db_factory):
    """`sort=relevance`: ada birebir eşleşen, yalnız amaçta geçenden önce gelir."""
    await _seed(db_factory)
    async with db_factory() as session:
        session.add_all(
            [
                DbCase(
                    id="case-name-match",
                    name="Kerem Boz",
                    purpose="genel kayıt",
                    legal_basis="self_check",
                    created_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
                ),
                DbCase(
                    id="case-purpose-match",
                    name="Başka Kayıt",
                    purpose="Kerem Boz hakkında not",
                    legal_basis="self_check",
                    created_at=datetime(2026, 11, 20, tzinfo=timezone.utc),
                ),
            ]
        )
        session.add_all(
            [
                DbTarget(
                    case_id="case-name-match", first_name="Kerem", last_name="Boz", hints={}
                ),
                DbTarget(
                    case_id="case-purpose-match", first_name="Ali", last_name="Veli", hints={}
                ),
            ]
        )
        await session.commit()

    res = await client.get(
        "/api/v1/cases", params={"q": "kerem boz", "sort": "relevance"}
    )
    assert [c["id"] for c in res.json()["items"]][:2] == [
        "case-name-match",
        "case-purpose-match",
    ]

    # q yokken relevance anlamsızdır: en yeni kayıt sırasına düşer, 422 değil.
    res = await client.get("/api/v1/cases", params={"sort": "relevance"})
    assert res.status_code == 200


# ---------------------------------------------------------------------------
# T-366: legacy döküm sınırı + q/offset üst sınırları
# ---------------------------------------------------------------------------


async def _seed_many(factory, count: int) -> None:
    """`count` adet minimum alanlı vaka (hızlı toplu ekleme)."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    async with factory() as session:
        session.add_all(
            [
                DbCase(
                    id=f"bulk-{index:04d}",
                    name=f"Toplu Vaka {index}",
                    purpose="bulk",
                    legal_basis="self_check",
                    created_at=base + timedelta(minutes=index),
                )
                for index in range(count)
            ]
        )
        await session.commit()


async def test_legacy_dokum_500_ile_sinirli(client, db_factory, caplog):
    await _seed_many(db_factory, 501)
    with caplog.at_level("WARNING", logger="showis.api.cases"):
        res = await client.get("/api/v1/cases?legacy=1")

    assert res.status_code == 200
    body = res.json()
    assert isinstance(body, list)
    assert len(body) == 500, f"legacy döküm 500 ile sınırlı olmalı ({len(body)})"
    assert "legacy dump truncated: total=501 limit=500" in caplog.text
    # Dizi (eski sözleşme) korunur; en yeni 500 (created_desc) döner
    assert body[0]["id"] == "bulk-0500"


async def test_legacy_sinir_altinda_uyari_yok(client, db_factory, caplog):
    await _seed_many(db_factory, 3)
    with caplog.at_level("WARNING", logger="showis.api.cases"):
        res = await client.get("/api/v1/cases?legacy=1")

    assert res.status_code == 200
    assert len(res.json()) == 3
    assert "legacy dump truncated" not in caplog.text


@pytest.mark.parametrize(
    "query,expected_status",
    [
        ("q=" + ("a" * 128), 200),  # tam sınır geçer
        ("q=" + ("a" * 129), 422),  # sınır aşımı
        ("offset=10000", 200),  # tam sınır geçer
        ("offset=10001", 422),  # derin offset reddedilir
    ],
)
async def test_q_ve_offset_ust_sinirlari(client, db_factory, query, expected_status):
    await _seed(db_factory)
    res = await client.get(f"/api/v1/cases?{query}")
    assert res.status_code == expected_status, res.text


async def test_offset_siniri_legacy_modda_da_gecerli(client, db_factory):
    await _seed(db_factory)
    res = await client.get("/api/v1/cases?legacy=1&offset=10001")
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# T-376: kontrol karakterleri (NUL vb.) 500/502 değil 422
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "a\x00b",  # NUL — asyncpg'de 500/502'ye yol açıyordu
        "a\x01b",  # C0 kontrol karakteri
        "a\x1fb",
        "a\x7fb",  # DEL
        "\x00",
    ],
)
async def test_q_kontrol_karakterleri_422(client, db_factory, raw):
    await _seed(db_factory)
    res = await client.get("/api/v1/cases", params={"q": raw})
    assert res.status_code == 422, res.text
    assert "kontrol karakteri" in res.json()["detail"]


@pytest.mark.parametrize(
    "params",
    [
        {"status": "act\x00ive"},
        {"mode": "O2\x00"},
        {"q": "ok", "status": "active\x01"},
    ],
)
async def test_diger_parametrelerde_kontrol_karakteri_422(client, db_factory, params):
    await _seed(db_factory)
    res = await client.get("/api/v1/cases", params=params)
    assert res.status_code == 422, res.text


@pytest.mark.parametrize("raw", ["İnceleme", "Ayşe", "emoji🔍", "a b", "50%"])
async def test_normal_unicode_kabul_edilir(client, db_factory, raw):
    await _seed(db_factory)
    res = await client.get("/api/v1/cases", params={"q": raw})
    assert res.status_code == 200, res.text
    assert set(res.json()) == {"items", "total", "limit", "offset"}


async def test_dbapi_error_handler_girdi_hatasini_422_yapar():
    """Girdi kaynaklı DB hataları sızsız 422; gerçek arıza 500 kalır (maskelenmez)."""
    from showis.main import dbapi_error_handler
    from sqlalchemy.exc import DataError, OperationalError
    from starlette.requests import Request

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/cases",
            "headers": [],
            "query_string": b"",
        }
    )

    input_exc = DataError(
        "SELECT 1", {}, Exception('invalid byte sequence for encoding "UTF8": 0x00')
    )
    response = await dbapi_error_handler(request, input_exc)
    assert response.status_code == 422
    body = response.body.decode()
    assert body == '{"detail":"Geçersiz girdi"}'
    # İç detay/sır sızmaz
    assert "UTF8" not in body and "0x00" not in body and "SELECT" not in body

    outage = OperationalError("SELECT 1", {}, Exception("connection refused"))
    response = await dbapi_error_handler(request, outage)
    assert response.status_code == 500
    assert "connection refused" not in response.body.decode()
