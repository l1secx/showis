"""JSON Schema + TypeScript tip üretimi (tek yönlü hat).

    Pydantic modelleri / enums.py
              │
              ├──▶ packages/contracts/schemas/*.json      (JSON Schema)
              └──▶ apps/web/lib/types.generated.ts        (TS tipleri)

Kurallar:
- `apps/web/lib/types.generated.ts` **tamamen üretilir** — elle düzenlenmez.
- `apps/web/lib/types.ts` el yazımı tipleri barındırır ve üretilen dosyayı
  `export *` ile dışa aktarır. Bu betik `types.ts`'e **dokunmaz**.
- Çıktı deterministiktir (aynı girdi → aynı dosya; test edilir).

Çalıştırma: `make schemas`
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from showis_contracts.enums import (
    EdgeType,
    EntityType,
    FindingStatus,
    ObservationKind,
    Platform,
    RiskTier,
)
from showis_contracts.schemas import MODELS, export_all_schemas

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
GENERATED_TS = REPO_ROOT / "apps" / "web" / "lib" / "types.generated.ts"

#: `enums.py`'deki StrEnum'lar — TS union olarak birebir üretilir.
ENUMS: list[type] = [RiskTier, Platform, ObservationKind, FindingStatus, EntityType, EdgeType]

_REF_PREFIX = "#/$defs/"

_HEADER = """/* OTOMATİK ÜRETİLDİ — ELLE DÜZENLEMEYİN.
 *
 * Kaynak: packages/contracts/showis_contracts (Pydantic modelleri + enums.py)
 * Üretim: `make schemas`  →  packages/contracts/scripts/export_schemas.py
 * El yazımı ek tipler: ./types.ts (bu dosyayı `export *` ile yeniden dışa aktarır)
 */
"""


def _quote(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _ts_union(values: list[str]) -> str:
    """`"a" | "b"` biçiminde, uzunsa satırlara bölünmüş union."""
    single = " | ".join(_quote(v) for v in values)
    if len(single) <= 88:
        return f" {single}"
    return "\n" + "\n".join(f"  | {_quote(v)}" for v in values)


def _ts_type(schema: dict[str, Any]) -> str:
    """JSON Schema parçasını TS tip ifadesine çevirir (basit yürüyücü)."""
    if not schema:
        return "unknown"

    if "$ref" in schema:
        return str(schema["$ref"]).split(_REF_PREFIX)[-1].split("/")[-1]

    for key in ("anyOf", "oneOf"):
        if key in schema:
            parts: list[str] = []
            for branch in schema[key]:
                branch_type = _ts_type(branch)
                if branch_type not in parts:
                    parts.append(branch_type)
            # `null` sonda kalsın: `string | null`
            parts.sort(key=lambda item: item == "null")
            return " | ".join(parts)

    if "const" in schema:
        return _quote(schema["const"])

    if "enum" in schema:
        return " | ".join(_quote(value) for value in schema["enum"])

    declared = schema.get("type")
    if isinstance(declared, list):
        parts = []
        for item in declared:
            mapped = _ts_type({"type": item})
            if mapped not in parts:
                parts.append(mapped)
        parts.sort(key=lambda item: item == "null")
        return " | ".join(parts)

    if declared == "string":
        return "string"
    if declared in ("number", "integer"):
        return "number"
    if declared == "boolean":
        return "boolean"
    if declared == "null":
        return "null"
    if declared == "array":
        inner = _ts_type(schema.get("items") or {})
        if "|" in inner:
            inner = f"({inner})"
        return f"{inner}[]"

    if declared == "object" or "properties" in schema or "additionalProperties" in schema:
        properties = schema.get("properties")
        if properties:
            # İç içe anonim nesne (endüstri standardı modellerde nadir).
            required = set(schema.get("required") or [])
            fields = []
            for name, field_schema in properties.items():
                optional = "" if name in required else "?"
                fields.append(f"{name}{optional}: {_ts_type(field_schema)}")
            return "{ " + "; ".join(fields) + " }"
        additional = schema.get("additionalProperties")
        if isinstance(additional, dict) and additional:
            return f"Record<string, {_ts_type(additional)}>"
        return "Record<string, unknown>"

    return "unknown"


def _interface(name: str, schema: dict[str, Any]) -> str:
    required = set(schema.get("required") or [])
    properties: dict[str, Any] = schema.get("properties") or {}
    lines: list[str] = []
    for field, field_schema in properties.items():
        optional = "" if field in required else "?"
        ts_type = _ts_type(field_schema)
        if field == "platform" and ts_type == "string":
            ts_type = "Platform"
        lines.append(f"  {field}{optional}: {ts_type};")
    body = "\n".join(lines) if lines else "  [key: string]: unknown;"
    return f"export interface {name} {{\n{body}\n}}"


def _normalized(schema: dict[str, Any]) -> dict[str, Any]:
    """`$defs` ve metadata farklarını yok sayarak karşılaştırma anahtarı."""
    ignored = ("$defs", "title", "description")
    return {key: value for key, value in schema.items() if key not in ignored}


def _collect_definitions() -> dict[str, dict[str, Any]]:
    """Modelleri ve `$defs` içindeki yardımcı nesneleri toplar.

    Kök model tanımı (MODELS) her zaman kazanır; aynı isim bilinmeyen bir
    yardımcı tipte iki farklı içerikle gelirse fail-closed davranır.
    """
    interfaces: dict[str, dict[str, Any]] = {}
    for model in MODELS:
        interfaces[model.__name__] = model.model_json_schema()

    helpers: dict[str, dict[str, Any]] = {}
    for schema in interfaces.values():
        for name, sub_schema in (schema.get("$defs") or {}).items():
            if "enum" in sub_schema:  # enum'lar enums.py'den üretilir
                continue
            if name in interfaces:
                continue  # kök model tanımı geçerli
            known = helpers.get(name)
            if known is not None:
                if _normalized(known) != _normalized(sub_schema):
                    raise RuntimeError(
                        f"'{name}' tanımı modeller arasında tutarsız — şema üretimi durduruldu"
                    )
                continue
            helpers[name] = sub_schema

    ordered = dict(interfaces)
    for name in sorted(helpers):
        ordered[name] = helpers[name]
    return ordered


def generate_typescript_definitions() -> str:
    """Üretilen TS içeriği (saf fonksiyon — testler için)."""
    blocks: list[str] = [_HEADER]

    blocks.append("// --- Enum birlikleri (enums.py) ---")
    for enum_cls in ENUMS:
        values = [member.value for member in enum_cls]
        blocks.append(f"export type {enum_cls.__name__} ={_ts_union(values)};")

    blocks.append("\n// --- Modeller (Pydantic) ---")
    interfaces = _collect_definitions()
    for name, schema in interfaces.items():
        blocks.append(_interface(name, schema))

    return "\n".join(blocks).rstrip() + "\n"


def write_typescript(path: Path = GENERATED_TS) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(generate_typescript_definitions(), encoding="utf-8")
    return path


def main() -> None:
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    schemas = export_all_schemas()
    for name, schema in schemas.items():
        out_file = SCHEMA_DIR / f"{name}.json"
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(schema, f, indent=2, ensure_ascii=False)
    print(f"Exported {len(schemas)} schemas to {SCHEMA_DIR}")

    written = write_typescript()
    print(f"Wrote TypeScript definitions to {written}")


if __name__ == "__main__":
    main()
