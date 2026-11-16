"""T-334: TS tip üretimi jeneratörünün regresyon testleri.

Test edilenler:
1. Enum'lar `enums.py`'den birebir TS union'a dönüşüyor (yeni Platform değerleri dahil).
2. Model alanları doğru TS tipine eşleniyor (`Finding.authenticity`, `list[str]`, `str | None`).
3. Üretim deterministik (idempotent) — iki çağrı aynı çıktı.
4. Jeneratör yalnız `types.generated.ts`'i hedefler; el yazımı `types.ts`'e dokunmaz.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
from showis_contracts.enums import EdgeType, EntityType, FindingStatus, Platform

SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "export_schemas.py"
)


def _load_generator():
    spec = importlib.util.spec_from_file_location("showis_export_schemas", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generated() -> str:
    return _load_generator().generate_typescript_definitions()


def _interface_block(source: str, name: str) -> str:
    match = re.search(rf"export interface {name} \{{(.*?)\n\}}", source, re.S)
    assert match, f"{name} arayüzü üretilmedi"
    return match.group(1)


def test_platform_union_has_every_enum_value(generated: str) -> None:
    match = re.search(r"export type Platform =(.*?);", generated, re.S)
    assert match, "Platform union üretilmedi"
    union = set(re.findall(r'"([^"]+)"', match.group(1)))
    assert union == {member.value for member in Platform}
    # T-308'de eklenen platformlar özellikle görünmeli
    assert {"stackoverflow", "bitbucket", "mastodon", "codeberg"} <= union


def test_other_enums_are_generated(generated: str) -> None:
    for enum_cls in (FindingStatus, EntityType, EdgeType):
        match = re.search(rf"export type {enum_cls.__name__} =(.*?);", generated, re.S)
        assert match, f"{enum_cls.__name__} union üretilmedi"
        assert set(re.findall(r'"([^"]+)"', match.group(1))) == {
            member.value for member in enum_cls
        }


def test_finding_authenticity_fields(generated: str) -> None:
    block = _interface_block(generated, "Finding")
    assert "authenticity?: number | null;" in block
    assert "authenticity_reasons?: string[];" in block
    assert "platform: Platform;" in block
    assert "status?: FindingStatus;" in block


def test_type_mapping_is_faithful(generated: str) -> None:
    evidence = _interface_block(generated, "Evidence")
    # datetime -> string; varsayılanı olan alanlar (JSON Schema `required` dışı) opsiyonel.
    assert "fetched_at?: string;" in evidence
    assert "http_status?: number | null;" in evidence  # int | None
    assert "truncated?: boolean;" in evidence
    assert "id: string;" in evidence  # zorunlu alan opsiyonel olmamalı

    entity = _interface_block(generated, "Entity")
    assert "attributes?: Record<string, unknown>;" in entity  # dict[str, Any]
    assert "evidence_ids?: string[];" in entity  # list[str]

    observation = _interface_block(generated, "Observation")
    assert "evidence?: Evidence[];" in observation  # list[Model] -> $ref


def test_generation_is_idempotent() -> None:
    generator = _load_generator()
    first = generator.generate_typescript_definitions()
    assert first == generator.generate_typescript_definitions()


def test_generator_targets_only_generated_file() -> None:
    generator = _load_generator()
    target = Path(generator.GENERATED_TS)
    assert target.name == "types.generated.ts"
    assert target.parent.name == "lib"
    assert target.name != "types.ts"
    assert "OTOMATİK ÜRETİLDİ" in generator.generate_typescript_definitions().splitlines()[0]
