"""MISP core-format dışa aktarma."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


def _attr_type(entity: Any) -> str:
    etype = str(getattr(entity, "type", "") or "").lower()
    platform = str(getattr(entity, "platform", "") or "").lower()
    if etype == "domain":
        return "domain"
    if etype == "ip":
        return "ip-src"
    if etype == "email":
        return "email-src"
    if platform == "github":
        return "github-username"
    if platform == "url":
        return "url"
    return "text"


def _attr_value(entity: Any) -> str:
    attrs = entity.attributes or {}
    etype = str(getattr(entity, "type", "") or "").lower()
    if etype == "domain":
        return str(attrs.get("domain") or entity.label or "")
    if etype == "ip":
        return str(attrs.get("ip") or entity.label or "")
    if etype == "email":
        return str(attrs.get("email") or attrs.get("public_email") or entity.label or "")
    return str(
        attrs.get("username")
        or attrs.get("login")
        or attrs.get("handle")
        or entity.label
        or entity.url
        or ""
    )


def build_misp_event(
    *,
    case: Any,
    target: Any,
    entities: list[Any],
    findings: list[Any],
) -> dict[str, Any]:
    findings_by_entity = {str(f.entity_id): f for f in findings}
    attributes: list[dict[str, Any]] = []
    for entity in entities:
        value = _attr_value(entity)
        if not value:
            continue
        finding = findings_by_entity.get(str(entity.id))
        attr: dict[str, Any] = {
            "uuid": str(uuid.uuid4()),
            "type": _attr_type(entity),
            "category": (
                "Person"
                if str(entity.type) in {"account", "person", "username"}
                else "Network activity"
            ),
            "value": value,
            "to_ids": bool(finding and finding.status == "confirmed"),
            "distribution": "0",
            "comment": (
                f"Showis {getattr(case, 'id', '')} · "
                f"{(finding.status if finding else 'entity')} · "
                f"score {round(float(finding.score), 2) if finding else '-'}"
            ),
        }
        if entity.url:
            attr["comment"] += f" · {entity.url}"
        attributes.append(attr)
    return {
        "Event": {
            "uuid": str(uuid.uuid5(uuid.NAMESPACE_URL, f"showis:{getattr(case, 'id', '')}")),
            "info": f"Showis — {getattr(case, 'name', getattr(case, 'id', ''))}",
            "date": datetime.now(timezone.utc).date().isoformat(),
            "distribution": "0",
            "threat_level_id": "2",
            "analysis": "1",
            "published": False,
            "Attribute": attributes,
            "Orgc": {"name": "Showis"},
        }
    }
