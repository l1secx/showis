"""Maltego local-transform XML üretimi."""

from __future__ import annotations

from html import escape
from typing import Any


def _field(name: str, value: Any) -> str:
    if value is None or value == "":
        return ""
    return (
        "<Field Name=\""
        + escape(str(name), quote=True)
        + "\" DisplayName=\""
        + escape(str(name), quote=True)
        + "\">"
        + escape(str(value))
        + "</Field>"
    )


def build_maltego_xml(
    *,
    case: Any,
    target: Any,
    entities: list[Any],
    findings: list[Any],
) -> str:
    findings_by_entity = {str(f.entity_id): f for f in findings}
    rows: list[str] = []
    target_name = getattr(target, "display_name", "") or getattr(case, "name", "")
    if target_name:
        rows.append(
            "<Entity Type=\"maltego.Phrase\">"
            f"<Value>{escape(str(target_name))}</Value>"
            "<AdditionalFields>"
            + _field("showis.kind", getattr(target, "kind", "target"))
            + _field("showis.case_id", getattr(case, "id", ""))
            + "</AdditionalFields></Entity>"
        )
    for entity in entities:
        finding = findings_by_entity.get(str(entity.id))
        fields = [
            _field("showis.entity_id", entity.id),
            _field("showis.platform", entity.platform),
            _field("showis.type", entity.type),
            _field("showis.url", entity.url),
        ]
        if finding is not None:
            fields.extend(
                [
                    _field("showis.finding_id", finding.id),
                    _field("showis.status", finding.status),
                    _field("showis.score", finding.score),
                    _field("showis.authenticity", finding.authenticity),
                ]
            )
        rows.append(
            "<Entity Type=\"maltego.Phrase\">"
            f"<Value>{escape(str(entity.label or entity.id))}</Value>"
            "<AdditionalFields>"
            + "".join(fields)
            + "</AdditionalFields></Entity>"
        )
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<MaltegoMessage><MaltegoTransformResponseMessage><Entities>"
        + "".join(rows)
        + "</Entities></MaltegoTransformResponseMessage></MaltegoMessage>"
    )
