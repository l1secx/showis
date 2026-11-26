"""API entegrasyon testleri — sqlite uzerinde gercek route'lar.

Kurulumun tamami (sys.path, MASTER_KEY, DB engine, `get_db` override, auth
token'i) `tests/integration/conftest.py` icindedir; bu dosya yalnizca davranisi
test eder (T-115). `client` fixture'i sunucunun bekledigi token'i basliklarla
yollar, bu yuzden auth acik veya kapali — iki durumda da yesil kalir.
"""

import uuid

import pytest
from showis.main import app


@pytest.mark.asyncio
async def test_case_crud_and_export(client):
    # Olustur
    res = await client.post(
        "/api/v1/cases",
        json={
            "name": "Test Vaka",
            "purpose": "entegrasyon testi",
            "legal_basis": "self_check",
            "authorization_confirmed": True,
            "retention_days": 7,
            "target": {"first_name": "Test", "last_name": "Kisi", "hints": {"city": "Ankara"}},
            "seeds": [{"platform": "github", "username": "testuser"}],
        },
    )
    assert res.status_code == 201, res.text
    case_id = res.json()["id"]

    # Detay
    res = await client.get(f"/api/v1/cases/{case_id}")
    assert res.status_code == 200
    body = res.json()
    assert body["target"]["first_name"] == "Test"
    assert body["seeds"][0]["platform"] == "github"

    # Liste — T-358: varsayılan zarf; eski düz liste `legacy=1` ile
    res = await client.get("/api/v1/cases")
    assert res.status_code == 200
    envelope = res.json()
    assert set(envelope) == {"items", "total", "limit", "offset"}
    assert any(c["id"] == case_id for c in envelope["items"])

    res = await client.get("/api/v1/cases?legacy=1")
    assert any(c["id"] == case_id for c in res.json())

    # Export
    res = await client.get(f"/api/v1/cases/{case_id}/export")
    assert res.status_code == 200
    assert res.json()["case"]["id"] == case_id

    # Graph: tarama varlığı yok; yalnız vaka havuzu (hedef tohumları) görünür.
    res = await client.get(f"/api/v1/cases/{case_id}/graph")
    assert res.status_code == 200
    assert all(node["data"]["origin"] == "pool" for node in res.json()["nodes"])

    # Audit
    res = await client.get(f"/api/v1/cases/{case_id}/audit")
    assert res.status_code == 200
    assert any(log["action"] == "case.create" for log in res.json())

    # Rapor (json)
    res = await client.post(f"/api/v1/cases/{case_id}/report", json={"format": "json"})
    assert res.status_code == 200, res.text
    report = res.json()
    assert report["format"] == "json"
    assert report["download_url"].startswith("/api/v1/reports/")

    # Rapor indir
    res = await client.get(report["download_url"])
    assert res.status_code == 200
    assert res.json()["case"]["id"] == case_id

    # PDF rapor
    res = await client.post(f"/api/v1/cases/{case_id}/report", json={"format": "pdf"})
    assert res.status_code == 200, res.text
    res = await client.get(res.json()["download_url"])
    assert res.status_code == 200
    assert res.content[:5] == b"%PDF-"

    # Sil
    res = await client.delete(f"/api/v1/cases/{case_id}")
    assert res.status_code == 204
    res = await client.get(f"/api/v1/cases/{case_id}")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_delete_case_removes_evidence_files(client, tmp_path, monkeypatch):
    monkeypatch.setenv("SHOWIS_EVIDENCE_DIR", str(tmp_path / "evidence"))
    res = await client.post(
        "/api/v1/cases",
        json={
            "name": "Kanit Vaka",
            "purpose": "silme testi",
            "legal_basis": "self_check",
            "authorization_confirmed": True,
            "target": {"first_name": "Kanit", "last_name": "Kisi", "hints": {}},
        },
    )
    assert res.status_code in (200, 201), res.text
    case_id = res.json()["id"]

    from showis.db.models import Evidence
    from showis.services.retention import _evidence_file_path

    storage_key = "evidence/abcd1234/0123456789abcdef.html"
    path = _evidence_file_path(storage_key)
    assert path is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"<html>kanit</html>")

    async with app.state.test_session_factory() as session:
        session.add(
            Evidence(
                case_id=case_id,
                kind="html",
                content_hash="0123456789abcdef",
                storage_key=storage_key,
            )
        )
        await session.commit()

    assert path.exists()
    res = await client.delete(f"/api/v1/cases/{case_id}")
    assert res.status_code == 204
    # DB cascade'i dosyalari silmez: dosya da kalkmis olmali
    assert not path.exists()


@pytest.mark.asyncio
async def test_settings_secret_masked(client):
    res = await client.post(
        "/api/v1/settings/providers",
        json={"provider": "github", "secret": "ghp_supersecret123456", "enabled": True},
    )
    assert res.status_code == 200
    body = res.json()
    assert "ghp_supersecret123456" not in str(body)
    assert body["secret_masked"]

    # Secret guncellenmeden birakilirsa korunur
    res = await client.post(
        "/api/v1/settings/providers",
        json={"provider": "github", "config": {"x": 1}, "enabled": True},
    )
    assert res.status_code == 200
    assert res.json()["secret_masked"] == body["secret_masked"]


@pytest.mark.asyncio
async def test_finding_patch_validation(client):
    res = await client.patch(f"/api/v1/findings/{uuid.uuid4()}", json={"status": "confirmed"})
    assert res.status_code == 404
    res = await client.patch(f"/api/v1/findings/{uuid.uuid4()}", json={"status": "bogus"})
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_legal_attestation_olmadan_vaka_reddedilir(client):
    res = await client.post(
        "/api/v1/cases",
        json={
            "name": "Onaysız Vaka",
            "purpose": "hukuki onay testi",
            "legal_basis": "self_check",
            "authorization_confirmed": False,
            "retention_days": 7,
            "target": {"first_name": "Test", "last_name": "Kisi"},
        },
    )
    assert res.status_code == 422
    assert "authorization_confirmed" in res.text or "onay" in res.text.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target",
    [
        {"kind": "email", "email": "Ada@Example.com"},
        {"kind": "phone", "phone": "+90 (555) 111-22-33"},
        {"kind": "domain", "domain": "Example.com."},
        {"kind": "ip", "ip": "192.0.2.1"},
    ],
)
async def test_target_identifier_tipleri_donusur(client, target):
    res = await client.post(
        "/api/v1/cases",
        json={
            "name": f"Kimlik {target['kind']}",
            "purpose": "kimlik türü testi",
            "legal_basis": "self_check",
            "authorization_confirmed": True,
            "target": target,
        },
    )
    assert res.status_code == 201, res.text
    case_id = res.json()["id"]
    detail = (await client.get(f"/api/v1/cases/{case_id}")).json()["target"]
    assert detail["kind"] == target["kind"]
    for key in ("email", "phone", "domain", "ip"):
        if key in target:
            expected = target[key]
            if key == "email":
                expected = expected.lower()
            if key == "phone":
                expected = "+905551112233"
            if key == "domain":
                expected = expected.lower().rstrip(".")
            assert detail[key] == expected


@pytest.mark.asyncio
async def test_gecersiz_domain_422(client):
    res = await client.post(
        "/api/v1/cases",
        json={
            "name": "Geçersiz Domain",
            "purpose": "kimlik türü testi",
            "legal_basis": "self_check",
            "authorization_confirmed": True,
            "target": {"kind": "domain", "domain": "not a domain"},
        },
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_case_owner_ve_yorumlar(client):
    res = await client.post(
        "/api/v1/cases",
        json={
            "name": "İş Birliği",
            "purpose": "yorum testi",
            "legal_basis": "self_check",
            "authorization_confirmed": True,
            "target": {"kind": "handle", "nickname": "collab"},
        },
    )
    assert res.status_code == 201, res.text
    case_id = res.json()["id"]

    owner = await client.patch(f"/api/v1/cases/{case_id}/owner", json={"owner": "analist-1"})
    assert owner.status_code == 200
    assert owner.json()["owner"] == "analist-1"
    detail = (await client.get(f"/api/v1/cases/{case_id}")).json()
    assert detail["owner"] == "analist-1"

    created = await client.post(
        f"/api/v1/cases/{case_id}/comments",
        json={"body": "İlk inceleme notu", "author": "analist-1"},
    )
    assert created.status_code == 201, created.text
    comments = (await client.get(f"/api/v1/cases/{case_id}/comments")).json()
    assert len(comments) == 1
    assert comments[0]["body"] == "İlk inceleme notu"
    # V-005/B-012: yazar istemci alanından değil, doğrulanmış aktörden gelir
    # (paylaşılan token yolu aktörü `api-token` yapar).
    assert comments[0]["author"] == "api-token"
