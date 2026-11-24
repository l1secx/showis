"""Rapor: hipotez + kanıt zinciri + çelişki bölümü (HTML/PDF/JSON) ve veri üretimi."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "api" / "tests"))

import test_pool_relations as _rel  # noqa: E402
import test_research_workspace as _workspace  # noqa: E402
from showis.db.models import EvidenceSnapshot, Hypothesis  # noqa: E402
from showis.services.reasoning.graph import upsert_relation  # noqa: E402
from showis.services.report_service import build_reasoning_block, build_report_data  # noqa: E402
from showis_engine.report import (  # noqa: E402
    generate_html_report,
    generate_json_report,
    generate_pdf_report,
    render_html_report,
)
from test_report_quality import HOSTILE, _report_data  # noqa: E402

db_factory = _workspace.db_factory
_seed_case = _workspace._seed_case
_item = _rel._item


def _reasoning() -> dict:
    return {
        "hypotheses": [
            {
                "statement": f"Aynı kişi {HOSTILE}",
                "confidence": 0.82,
                "status": "proposed",
                "rationale": "iki bağımsız kaynak",
                "supporting": [
                    {"rule": "same_handle", "note": "aynı kullanıcı adı", "weight": 2.0}
                ],
                "contradicting": [{"rule": "location", "note": "farklı şehir", "weight": 1.0}],
                "next_checks": ["Profil fotoğrafını karşılaştır"],
            }
        ],
        "relations": [
            {
                "type": "same_person",
                "src": "github ada",
                "dst": "gitlab ada",
                "confidence": 0.7,
                "status": "candidate",
                "evidence": [{"rule": "same_handle", "note": "aynı kullanıcı adı", "weight": 2.0}],
            }
        ],
        "contradictions": [
            {"src": "Ankara", "dst": "İzmir", "status": "proposed", "message": "farklı konum"}
        ],
        "snapshots": [
            {
                "url": "https://example.org/p",
                "title": "Profil",
                "sha256": "a" * 64,
                "fetched_at": "2026-11-24T10:00:00+00:00",
            }
        ],
    }


def test_html_without_reasoning_keeps_original_numbering():
    html = render_html_report(_report_data())
    assert "7. Sınırlamalar" in html and "Kanıt Zinciri" not in html


def test_html_reasoning_section_is_escaped_and_complete():
    data = _report_data()
    data["reasoning"] = _reasoning()
    html = render_html_report(data)
    assert "7. Hipotezler, Kanıt Zinciri ve Çelişkiler" in html
    assert "8. Sınırlamalar" in html
    assert "<script>alert" not in html
    assert "0.82" in html and "aynı kullanıcı adı (+2.0)" in html and "farklı şehir (−1.0)" in html
    assert "Ankara ↔ İzmir" in html and "aaaaaaaaaaaaaaaa…" in html
    assert generate_html_report(data).startswith(b"<!DOCTYPE html>")


def test_pdf_and_json_include_reasoning():
    data = _report_data()
    data["reasoning"] = _reasoning()
    pdf = generate_pdf_report(data)
    assert pdf.startswith(b"%PDF") and len(pdf) > len(generate_pdf_report(_report_data()))
    payload = json.loads(generate_json_report(data))
    assert payload["reasoning"]["hypotheses"][0]["confidence"] == 0.82


@pytest.mark.asyncio
async def test_reasoning_block_only_trusted_relations(db_factory):
    case_id = await _seed_case(db_factory)
    async with db_factory() as db:
        a = await _item(db, case_id, "username", "keremboz", label="gh keremboz")
        b = await _item(db, case_id, "url", "https://x.com/keremboz", label="x keremboz")
        c = await _item(db, case_id, "location", "Ankara", label="Ankara")
        d = await _item(db, case_id, "location", "İzmir", label="İzmir")
        evidence = [{"rule": "same_handle", "source": "s", "weight": 2.0, "note": "aynı ad"}]
        for dst, rel_type, status in (
            (b, "same_person", "candidate"),
            (c, "located_in", "proposed"),
            (d, "located_in", "rejected"),
        ):
            await upsert_relation(
                db,
                case_id=case_id,
                src_item_id=a.id,
                dst_item_id=dst.id,
                type=rel_type,
                evidence=evidence,
                status=status,
            )
        await upsert_relation(
            db,
            case_id=case_id,
            src_item_id=c.id,
            dst_item_id=d.id,
            type="contradicts",
            evidence=[{"rule": "x", "source": "y", "weight": 1.0, "note": "farklı konum"}],
            status="proposed",
        )
        db.add(Hypothesis(case_id=case_id, statement="Açık", confidence=0.6, status="proposed"))
        db.add(
            Hypothesis(case_id=case_id, statement="Reddedilmiş", confidence=0.9, status="rejected")
        )
        db.add(
            EvidenceSnapshot(
                case_id=case_id, url="https://example.org/s", sha256="b" * 64, text="gizli metin"
            )
        )
        await db.commit()

        block = await build_reasoning_block(db, case_id)
        assert [h["statement"] for h in block["hypotheses"]] == ["Açık"]
        # same_person simetriktir: uç sırası kimliklere bağlı.
        assert [(frozenset((r["src"], r["dst"])), r["status"]) for r in block["relations"]] == [
            (frozenset({"gh keremboz", "x keremboz"}), "candidate")
        ]
        assert block["contradictions"][0]["message"] == "farklı konum"
        assert block["snapshots"][0]["url"] == "https://example.org/s"
        assert "gizli metin" not in json.dumps(block)  # arşiv metni rapora konmaz

        data = await build_report_data(db, case_id)
        assert data["reasoning"]["relations"]


@pytest.mark.asyncio
async def test_report_data_omits_empty_reasoning(db_factory):
    case_id = await _seed_case(db_factory)
    async with db_factory() as db:
        assert "reasoning" not in await build_report_data(db, case_id)
