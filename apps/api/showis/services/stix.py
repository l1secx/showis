"""STIX 2.1 dışa aktarma — vaka entity/bulgu/kanıtlarını taşınabilir hale getirir."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

STIX_NAMESPACE = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")
STIX_VERSION = "2.1"


def _id(kind: str, value: str) -> str:
    digest = uuid.uuid5(STIX_NAMESPACE, f"{kind}:{value}")
    return f"{kind}--{digest}"


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def _domain_from_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        from urllib.parse import urlparse

        host = urlparse(url).hostname
        return host.lower() if host else None
    except ValueError:
        return None


def _entity_object(entity: Any) -> dict[str, Any] | None:
    attrs = entity.attributes or {}
    etype = str(entity.type or "account")
    platform = str(entity.platform or "unknown")
    created = _iso(getattr(entity, "first_seen", None)) or _iso(datetime.now(timezone.utc))
    base = {
        "spec_version": STIX_VERSION,
        "created": created,
        "modified": created,
        "labels": [platform],
        "x_showis_entity_id": entity.id,
    }
    if etype == "domain":
        value = str(attrs.get("domain") or entity.label or "").strip()
        if not value:
            return None
        return {
            **base,
            "type": "domain-name",
            "id": _id("domain-name", value),
            "value": value,
        }
    if etype == "ip":
        value = str(attrs.get("ip") or entity.label or "").strip()
        if not value:
            return None
        try:
            import ipaddress

            version = ipaddress.ip_address(value).version
        except ValueError:
            return None
        kind = "ipv4-addr" if version == 4 else "ipv6-addr"
        return {**base, "type": kind, "id": _id(kind, value), "value": value}
    if etype == "email" or attrs.get("email"):
        value = str(attrs.get("email") or attrs.get("public_email") or entity.label or "")
        if "@" not in value:
            return None
        return {
            **base,
            "type": "email-addr",
            "id": _id("email-addr", value),
            "value": value,
        }
    if etype in {"account", "username"} or attrs.get("username"):
        login = str(
            attrs.get("username")
            or attrs.get("login")
            or attrs.get("handle")
            or entity.label
            or ""
        ).strip()
        if not login:
            return None
        obj = {
            **base,
            "type": "user-account",
            "id": _id("user-account", f"{platform}:{login}"),
            "account_login": login,
            "account_type": platform,
        }
        if entity.url:
            obj["x_showis_url"] = entity.url
        if attrs.get("name"):
            obj["display_name"] = str(attrs["name"])
        return obj
    if entity.url:
        domain = _domain_from_url(entity.url)
        if domain:
            return {
                **base,
                "type": "domain-name",
                "id": _id("domain-name", domain),
                "value": domain,
            }
    return None


def build_stix_bundle(
    *,
    case: Any,
    target: Any,
    entities: list[Any],
    findings: list[Any],
    evidence: list[Any],
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    objects: list[dict[str, Any]] = []
    report_refs: list[str] = []

    target_name = (
        getattr(target, "display_name", "")
        or getattr(case, "name", "")
        or str(case.id)
    )
    identity_id = _id("identity", str(case.id))
    identity = {
        "type": "identity",
        "spec_version": STIX_VERSION,
        "id": identity_id,
        "created": _iso(getattr(case, "created_at", None)) or _iso(now),
        "modified": _iso(getattr(case, "created_at", None)) or _iso(now),
        "name": target_name,
        "identity_class": "individual",
        "x_showis_case_id": case.id,
    }
    objects.append(identity)
    report_refs.append(identity_id)

    entity_by_id: dict[str, str] = {}
    for entity in entities:
        obj = _entity_object(entity)
        if obj is None:
            continue
        objects.append(obj)
        report_refs.append(obj["id"])
        entity_by_id[str(entity.id)] = obj["id"]

    relationship_objects: list[dict[str, Any]] = []
    for finding in findings:
        entity_ref = entity_by_id.get(str(finding.entity_id))
        pattern = None
        if finding.url:
            pattern = f"[url:value = '{finding.url}']"
        elif entity_ref:
            pattern = None
        label = str(finding.label or "").replace("'", "\\'")
        if not pattern and label:
            pattern = f"[user-account:account_login = '{label}']"
        if not pattern:
            continue
        indicator_id = _id("indicator", str(finding.id))
        indicator = {
            "type": "indicator",
            "spec_version": STIX_VERSION,
            "id": indicator_id,
            "created": _iso(getattr(finding, "created_at", None)) or _iso(now),
            "modified": _iso(getattr(finding, "verified_at", None)) or _iso(now),
            "name": finding.label or finding.id,
            "pattern": pattern,
            "pattern_type": "stix",
            "valid_from": _iso(getattr(finding, "created_at", None)) or _iso(now),
            "confidence": max(0, min(100, int(round(float(finding.score or 0) * 100)))),
            "labels": [str(finding.platform), str(finding.status)],
            "x_showis_finding_id": finding.id,
            "x_showis_authenticity": getattr(finding, "authenticity", None),
        }
        if finding.url:
            indicator["external_references"] = [
                {"source_name": "showis", "url": finding.url}
            ]
        objects.append(indicator)
        report_refs.append(indicator_id)
        if entity_ref:
            rel = {
                "type": "relationship",
                "spec_version": STIX_VERSION,
                "id": _id("relationship", f"related:{indicator_id}:{entity_ref}"),
                "created": _iso(now),
                "modified": _iso(now),
                "relationship_type": "related-to",
                "source_ref": indicator_id,
                "target_ref": entity_ref,
            }
            relationship_objects.append(rel)
    objects.extend(relationship_objects)

    for ev in evidence:
        if not getattr(ev, "content_hash", None):
            continue
        artifact = {
            "type": "artifact",
            "spec_version": STIX_VERSION,
            "id": _id("artifact", str(ev.id)),
            "created": _iso(getattr(ev, "fetched_at", None)) or _iso(now),
            "modified": _iso(getattr(ev, "fetched_at", None)) or _iso(now),
            "hashes": {"SHA-256": str(ev.content_hash)},
            "x_showis_evidence_id": ev.id,
            "x_showis_source_url": ev.source_url,
        }
        objects.append(artifact)
        report_refs.append(artifact["id"])

    report = {
        "type": "report",
        "spec_version": STIX_VERSION,
        "id": _id("report", str(case.id)),
        "created": _iso(now),
        "modified": _iso(now),
        "name": f"Showis — {getattr(case, 'name', case.id)}",
        "description": getattr(case, "purpose", "") or "",
        "published": _iso(now),
        "object_refs": sorted(set(report_refs)),
        "labels": ["osint", str(getattr(case, "legal_basis", ""))],
    }
    objects.append(report)

    return {
        "type": "bundle",
        "id": _id("bundle", f"{case.id}:{now.isoformat()}"),
        "objects": objects,
    }
