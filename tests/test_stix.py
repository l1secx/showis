"""STIX 2.1 export testleri."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from showis.services.stix import build_stix_bundle


def test_stix_bundle_entity_ve_indicator_uretir():
    case = SimpleNamespace(
        id="case-1",
        name="Vaka",
        purpose="test",
        legal_basis="self_check",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    target = SimpleNamespace(display_name="Ada Lovelace")
    entities = [
        SimpleNamespace(
            id="github:ada",
            type="account",
            platform="github",
            label="ada",
            url="https://github.com/ada",
            attributes={"username": "ada", "name": "Ada Lovelace"},
            first_seen=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        SimpleNamespace(
            id="web:domain:example.com",
            type="domain",
            platform="web",
            label="example.com",
            url="https://example.com",
            attributes={"domain": "example.com"},
            first_seen=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
    ]
    findings = [
        SimpleNamespace(
            id="finding:github:ada",
            entity_id="github:ada",
            platform="github",
            label="ada",
            url="https://github.com/ada",
            status="confirmed",
            score=0.9,
            authenticity=0.8,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            verified_at=None,
        )
    ]
    evidence = [
        SimpleNamespace(
            id="ev-1",
            content_hash="a" * 64,
            source_url="https://github.com/ada",
            fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    ]
    bundle = build_stix_bundle(
        case=case, target=target, entities=entities, findings=findings, evidence=evidence
    )
    assert bundle["type"] == "bundle"
    types = [obj["type"] for obj in bundle["objects"]]
    assert "identity" in types
    assert "user-account" in types
    assert "domain-name" in types
    assert "indicator" in types
    assert "artifact" in types
    assert "report" in types
    assert "relationship" in types
    user = next(obj for obj in bundle["objects"] if obj["type"] == "user-account")
    assert user["account_login"] == "ada"
    indicator = next(obj for obj in bundle["objects"] if obj["type"] == "indicator")
    assert indicator["confidence"] == 90
    assert indicator["external_references"][0]["url"] == "https://github.com/ada"
