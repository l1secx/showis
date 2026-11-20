"""T-313: rapor kalitesi (yönetici özeti, tablolar, kronoloji) + XSS/SSTI testleri."""

from __future__ import annotations

import json

import pytest
from showis_engine.report import (
    build_report_sections,
    generate_html_report,
    generate_json_report,
    generate_pdf_report,
    render_html_report,
    render_target_block,
    report_env,
)

HOSTILE = '<script>alert("xss")</script>{{7*7}}${7*7}'


def _report_data(*, with_edges: bool = True, with_dates: bool = True) -> dict:
    entities = [
        {
            "id": "github:ada",
            "type": "account",
            "platform": "github",
            "label": "ada",
            "url": "https://github.com/ada",
            "attributes": {
                "username": "ada",
                "name": "Ada Lovelace",
                "bio": HOSTILE,
                **({"created_at": "2015-03-04T10:00:00+00:00"} if with_dates else {}),
            },
            "evidence_ids": ["ev-1", "ev-2"],
        },
        {
            "id": "gitlab:ada",
            "type": "account",
            "platform": "gitlab",
            "label": "ada",
            "url": "https://gitlab.com/ada",
            "attributes": {
                "username": "ada",
                **({"created_at": 1425000000} if with_dates else {}),
            },
            "evidence_ids": ["ev-1"],
        },
        {
            "id": "mastodon:ada",
            "type": "account",
            "platform": "mastodon",
            "label": "Ada L.",
            "url": "https://mastodon.social/@ada",
            "attributes": {"username": "ada", "followers_count": 120},
            "evidence_ids": [],
        },
    ]
    findings = [
        {
            "id": "finding:github:ada",
            "entity_id": "github:ada",
            "platform": "github",
            "url": "https://github.com/ada",
            "label": "ada",
            "status": "confirmed",
            "score": 0.91,
            "breakdown": {"name": 0.4, "username": 0.3, "total": 0.91},
            "explanation": "GitHub profili isim ve bio ile eşleşiyor",
            "evidence_ids": ["ev-1", "ev-2"],
        },
        {
            "id": "finding:gitlab:ada",
            "entity_id": "gitlab:ada",
            "platform": "gitlab",
            "url": "https://gitlab.com/ada",
            "label": "ada",
            "status": "candidate",
            "score": 0.72,
            "breakdown": {"username": 0.3},
            "explanation": None,
            "evidence_ids": ["ev-1"],
        },
        {
            "id": "finding:mastodon:ada",
            "entity_id": "mastodon:ada",
            "platform": "mastodon",
            "url": "https://mastodon.social/@ada",
            "label": "Ada L.",
            "status": "lead",
            "score": 0.45,
            "breakdown": {},
            "explanation": None,
            "evidence_ids": [],
        },
        {
            "id": "finding:web:ada",
            "entity_id": "web:ada",
            "platform": "web",
            "url": None,
            "label": HOSTILE,
            "status": "rejected",
            "score": 0.2,
            "breakdown": {"username": 0.2},
            "explanation": HOSTILE,
            "evidence_ids": [],
        },
    ]
    evidence = [
        {
            "id": "ev-1",
            "source_url": "https://github.com/ada",
            "kind": "json",
            "content_hash": "a" * 64,
            "storage_key": "evidence/x.json",
            "http_status": 200,
            "fetched_at": "2026-11-01T10:00:00+00:00",
            "expires_at": "2026-12-01T10:00:00+00:00",
        },
        {
            "id": "ev-2",
            "source_url": "https://github.com/ada?tab=repositories",
            "kind": "json",
            "content_hash": "b" * 64,
            "storage_key": "evidence/y.json",
            "http_status": 200,
            "fetched_at": "2026-11-02T10:00:00+00:00",
            "expires_at": "2026-12-05T10:00:00+00:00",
        },
    ]
    edges = (
        [
            {
                "id": "edge:1",
                "type": "same_person",
                "source_entity_id": "github:ada",
                "target_entity_id": "gitlab:ada",
                "confidence": 0.88,
                "reason": "aynı doğrulanmış e-posta",
            }
        ]
        if with_edges
        else []
    )
    return {
        "case": {
            "id": "case-1",
            "name": HOSTILE,
            "purpose": "Kendini kontrol",
            "legal_basis": "self_check",
            "notes": None,
            "status": "active",
            "retention_days": 30,
            "created_at": "2026-11-01T09:00:00+00:00",
        },
        "target": {"first_name": "Ada", "last_name": "Lovelace", "hints": {"city": "London"}},
        "entities": entities,
        "findings": findings,
        "evidence": evidence,
        "edges": edges,
        "scan": {
            "id": "scan-1",
            "mode": "O2",
            "status": "completed",
            "started_at": "2026-11-16T21:00:00+00:00",
            "finished_at": "2026-11-16T21:04:00+00:00",
        },
        "generated_at": "2026-11-16T22:00:00+00:00",
    }


def _render_report(target: dict) -> str:
    """HTML raporu + PDF hedef bloğu tek metinde (Task 9: etiket her iki yerde)."""
    data = _report_data()
    data["target"] = target
    return render_html_report(data) + render_target_block(data)


def _handle_target_data() -> dict:
    return {"kind": "handle", "nickname": "kerem", "first_name": "", "last_name": ""}


# ---------------------------------------------------------------------------
# 1. Yönetici özeti
# ---------------------------------------------------------------------------


def test_executive_summary_alanlari_dolu():
    sections = build_report_sections(_report_data())
    summary = sections["executive_summary"]

    assert summary["target_name"] == "Ada Lovelace"
    assert summary["mode"] == "O2"
    assert summary["scan_started_at"].startswith("2026-11-16T21:00")
    assert summary["totals"] == {"entities": 3, "platforms": 3, "findings": 4, "evidence": 2}
    assert summary["status_counts"]["confirmed"] == 1
    assert summary["status_counts"]["candidate"] == 1
    assert summary["status_counts"]["lead"] == 1
    assert summary["status_counts"]["rejected"] == 1


def test_executive_summary_handle_hedefinde_nick_yazar():
    """İsim yoksa `target_name` boş kalmaz: `@nick` basılır, `None` asla."""
    data = _report_data()
    data["target"] = _handle_target_data()
    summary = build_report_sections(data)["executive_summary"]
    assert summary["target_name"] == "@kerem"


def test_report_renders_nick_when_name_missing():
    html = _render_report(target=_handle_target_data())
    assert "@kerem" in html and "None" not in html and "Ad Soyad:</b> <br/>" not in html


def test_html_report_renders_nick_for_handle_only_target():
    """HTML raporu (PDF hedef bloğundan bağımsız) handle hedefinde `@nick` yazar.

    R-06: tüm HTML'de `@kerem` aramak zayıftı — entity etiketi/URL de aynı dizeyi
    içerebilir. Yönetici özetinin **hedef satırı** hedeflenir.
    """
    data = _report_data()
    data["target"] = _handle_target_data()
    # Diğer bölümler nick'i içermese de test geçmeli: asıl kanıt hedef satırı.
    for entity in data["entities"]:
        entity["label"] = "anonim"
    html = render_html_report(data)
    assert "<th>Hedef</th><td>@kerem</td>" in html
    assert "@@kerem" not in html


def test_en_yuksek_bes_bulgu_siralanir():
    data = _report_data()
    # 6 bulguya cikar -> ilk 5 dondurulmeli
    data["findings"].append(
        {
            "id": "finding:extra",
            "entity_id": "x",
            "platform": "npm",
            "url": None,
            "label": "ada",
            "status": "candidate",
            "score": 0.61,
            "breakdown": {},
            "explanation": None,
        }
    )
    summary = build_report_sections(data)["executive_summary"]

    top = summary["top_findings"]
    assert len(top) == 5
    assert [item["score"] for item in top] == sorted(
        (item["score"] for item in top), reverse=True
    )
    assert top[0]["platform"] == "github"
    assert top[0]["reason"].startswith("GitHub profili")
    # gerekçe yoksa sinyal kirilimindan uretilir
    assert any(item["reason"].startswith("En güçlü sinyal") for item in top)


def test_guven_araligi_yorumu():
    sections = build_report_sections(_report_data())
    note = sections["identity_links"]["confidence_note"]
    assert "3 platformda aynı kullanıcı adı" in note
    assert "yüksek olasılıklı" in note

    # Tek platform -> dusuk olasilik yorumu
    data = _report_data()
    data["entities"] = data["entities"][:1]
    low = build_report_sections(data)["identity_links"]["confidence_note"]
    assert "düşük olasılıklı" in low


# ---------------------------------------------------------------------------
# 2. Platform dağılımı
# ---------------------------------------------------------------------------


def test_platform_dagilim_tablosu():
    rows = build_report_sections(_report_data())["platform_distribution"]
    by_platform = {row["platform"]: row for row in rows}

    assert [row["platform"] for row in rows][0] == "github"  # en yüksek skor önce
    assert by_platform["github"] == {
        "platform": "github",
        "findings": 1,
        "max_score": 0.91,
        "avg_score": 0.91,
    }
    assert by_platform["mastodon"]["max_score"] == 0.45
    assert set(by_platform) == {"github", "gitlab", "mastodon", "web"}


# ---------------------------------------------------------------------------
# 3. Kimlik / çapraz bağlantı tablosu
# ---------------------------------------------------------------------------


def test_cross_link_edge_tablosu_dolar():
    links = build_report_sections(_report_data())["identity_links"]
    assert len(links["edges"]) == 1
    edge = links["edges"][0]
    assert edge["type"] == "same_person"
    assert edge["source"] == "github:ada"
    assert edge["target"] == "gitlab:ada"
    assert edge["confidence"] == 0.88
    assert edge["reason"] == "aynı doğrulanmış e-posta"


def test_edge_yoksa_paylasilan_kullanici_adi_grubu():
    links = build_report_sections(_report_data(with_edges=False))["identity_links"]
    assert links["edges"] == []
    groups = links["shared_username_groups"]
    assert groups == [
        {"username": "ada", "platforms": ["github", "gitlab", "mastodon"], "entities": 3}
    ]


# ---------------------------------------------------------------------------
# 4. Kronoloji
# ---------------------------------------------------------------------------


def test_kronoloji_tarihe_gore_siralanir_ve_epoch_cevrilir():
    timeline = build_report_sections(_report_data())["timeline"]
    assert [row["platform"] for row in timeline] == ["gitlab", "github"]
    assert timeline[0]["date"].startswith("2015-02-27")  # epoch 1425000000 -> ISO
    assert timeline[1]["date"].startswith("2015-03-04")
    assert all("T" in row["date"] for row in timeline)


def test_kronoloji_yoksa_bolum_bos():
    assert build_report_sections(_report_data(with_dates=False))["timeline"] == []


# ---------------------------------------------------------------------------
# 5. Kanıt özeti
# ---------------------------------------------------------------------------


def test_kanit_ozeti_sayilari_ve_retention():
    evidence_summary = build_report_sections(_report_data())["evidence_summary"]
    assert evidence_summary["total"] == 2
    assert evidence_summary["linked_to_entities"] == 2
    assert evidence_summary["retention_until"] == "2026-12-05T10:00:00+00:00"
    assert evidence_summary["by_entity"][0] == {
        "entity_id": "github:ada",
        "platform": "github",
        "label": "ada",
        "evidence": 2,
    }


def test_kanit_yoksa_retention_case_saklamasindan_hesaplanir():
    data = _report_data()
    data["evidence"] = []
    data["entities"] = [dict(entity, evidence_ids=[]) for entity in data["entities"]]
    summary = build_report_sections(data)["evidence_summary"]
    assert summary["total"] == 0
    assert summary["retention_until"].startswith("2026-12-01")  # created_at + 30 gün


# ---------------------------------------------------------------------------
# 6. Güvenlik: XSS / SSTI kaçışlama
# ---------------------------------------------------------------------------


def test_jinja_autoescape_acik():
    assert report_env.autoescape is True


def test_html_raporu_xss_ve_ssti_kacislar():
    html_output = render_html_report(_report_data())

    # Ham etiket/ifade asla çıkmaz
    assert "<script>" not in html_output
    assert "<script>alert" not in html_output
    assert "&lt;script&gt;" in html_output
    # Şablon ifadesi DEĞERLENDİRİLMEZ (49 üretilmez), literal kalır
    assert "{{7*7}}" in html_output
    assert "49" not in html_output
    assert "${7*7}" in html_output
    # Diğer bölümler de basıldı
    assert "Yönetici Özeti" in html_output
    assert "Platform Dağılımı" in html_output
    assert "Kronoloji" in html_output


def test_html_rapor_utf8_bytes():
    payload = generate_html_report(_report_data())
    assert payload.startswith(b"<!DOCTYPE html>")
    assert "Ada Lovelace".encode() in payload


def test_pdf_hostile_veriyle_uretilir():
    pdf = generate_pdf_report(_report_data())
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1500
    assert b"<script>" not in pdf


# ---------------------------------------------------------------------------
# 7. JSON sözleşmesi (geriye uyumlu) + dosya adı güvenliği
# ---------------------------------------------------------------------------


def test_json_raporda_eski_ve_yeni_alanlar():
    data = _report_data()
    payload = json.loads(generate_json_report(data).decode("utf-8"))

    # Eski alanlar korunur
    for key in ("case", "target", "entities", "findings", "evidence", "generated_at"):
        assert key in payload
    assert payload["case"]["name"] == HOSTILE
    # Yeni alanlar eklenir
    for key in (
        "executive_summary",
        "platform_distribution",
        "identity_links",
        "timeline",
        "evidence_summary",
    ):
        assert key in payload
    assert payload["executive_summary"]["totals"]["platforms"] == 3
    # JSON veri olarak bozulmaz (kaçışlama metin seviyesinde kalır)
    assert payload["findings"][3]["label"] == HOSTILE


def test_json_raporda_bolumler_tekrar_hesaplanmaz():
    data = _report_data()
    data["executive_summary"] = {"already": "present"}
    payload = json.loads(generate_json_report(data).decode("utf-8"))
    assert payload["executive_summary"] == {"already": "present"}


def test_dosya_adi_path_traversal_temizlenir(tmp_path, monkeypatch):
    import showis.services.report_service as svc

    assert svc.safe_case_token("../../etc/passwd") == "etcpasswd"
    assert svc.safe_case_token("..%2f..%2fetc") == "2f2fetc"
    assert svc.safe_case_token("") == "case"
    assert len(svc.safe_case_token("a" * 200)) == 64

    monkeypatch.setattr(svc, "REPORT_DIR", tmp_path / "reports")
    (tmp_path / "reports").mkdir()
    path = svc._report_path("../../evil", "pdf")
    # T-316: vaka klasoru; yol daima REPORT_DIR icinde
    assert path.parent == (tmp_path / "reports" / "evil").resolve()
    assert (tmp_path / "reports").resolve() in path.parents
    assert ".." not in path.name


@pytest.mark.parametrize(
    "token,expected",
    [
        ("case-abc_123", "case-abc_123"),
        ("a/b\\c", "abc"),
        ("cas e", "case"),
        ("....", ""),
    ],
)
def test_safe_case_token_parametrik(token, expected):
    from showis.services.report_service import safe_case_token

    result = safe_case_token(token)
    assert result == (expected or "case")
