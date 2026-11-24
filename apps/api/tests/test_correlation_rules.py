"""Korelasyon kuralları YAML: yükleme, doğrulama, varsayılana düşme."""

from __future__ import annotations

import pytest
from showis.db.models import PoolItem
from showis.services.reasoning import correlate
from showis.services.reasoning.correlate import (
    DEFAULT_RULES,
    RULES_PATH,
    RulesError,
    load_rules,
    parse_rules,
    run_rules,
)


def _item(item_id: str, kind: str, value: str, **attributes: str) -> PoolItem:
    return PoolItem(
        id=item_id,
        case_id="c",
        kind=kind,
        value=value,
        normalized=value.casefold(),
        label=value,
        status="proposed",
        attributes=attributes,
        provenance={},
    )


def _doc(*rules: dict) -> dict:
    return {"version": 1, "rules": list(rules)}


def test_shipped_yaml_matches_python_defaults():
    loaded = load_rules(RULES_PATH)
    defaults = {r.id: r.weight for r in DEFAULT_RULES}
    assert {r.id: r.weight for r in loaded if r.id in defaults} == defaults
    assert "same_phone" in {r.id for r in loaded}


def test_missing_or_invalid_file_falls_back(tmp_path, caplog):
    assert load_rules(tmp_path / "yok.yaml") == DEFAULT_RULES
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 2\nrules: []\n", encoding="utf-8")
    assert load_rules(bad) == DEFAULT_RULES
    broken = tmp_path / "broken.yaml"
    broken.write_text("rules: [unclosed", encoding="utf-8")
    assert load_rules(broken) == DEFAULT_RULES


@pytest.mark.parametrize(
    "rule",
    [
        {"id": "nope", "weight": 1.0},  # bilinmeyen yerleşik
        {"id": "same_handle", "weight": 0},
        {"id": "same_handle", "weight": 9},
        {"id": "same_handle", "weight": "2"},
        {"id": "same_handle", "weight": True},
        {"id": "same_handle", "weight": 1, "enabled": "yes"},
        {"id": "Bad Id", "weight": 1},
        {"id": "x", "type": "eval", "weight": 1},
        {
            "id": "x",
            "type": "shared_attribute",
            "weight": 1,
            "attribute": "a b",
            "relation": "same_person",
        },
        {
            "id": "x",
            "type": "shared_attribute",
            "weight": 1,
            "attribute": "a",
            "relation": "contradicts",
        },
        {
            "id": "x",
            "type": "shared_attribute",
            "weight": 1,
            "attribute": "a",
            "relation": "kız_kardeş",
        },
    ],
)
def test_invalid_rules_rejected(rule):
    with pytest.raises(RulesError):
        parse_rules(_doc(rule))


def test_duplicate_and_empty_rejected():
    with pytest.raises(RulesError):
        parse_rules(_doc({"id": "same_handle", "weight": 1}, {"id": "same_handle", "weight": 2}))
    with pytest.raises(RulesError):
        parse_rules({"version": 1, "rules": []})


def test_disabled_rule_is_skipped_and_weight_overrides(monkeypatch):
    rules = parse_rules(
        _doc(
            {"id": "same_handle", "weight": 4.0},
            {"id": "same_avatar", "weight": 1.0, "enabled": False},
        )
    )
    monkeypatch.setattr(correlate, "RULES", rules)
    items = [
        _item("a", "url", "https://github.com/keremboz", username="keremboz", platform="github"),
        _item("b", "url", "https://x.com/keremboz", username="keremboz", platform="x"),
        _item("c", "url", "https://a.example/1", avatar_dhash="ff"),
        _item("d", "url", "https://b.example/1", avatar_dhash="ff"),
    ]
    matches = run_rules(items)
    assert [(m.rule, m.weight) for m in matches] == [("same_handle", 4.0)]


def test_shared_attribute_rule_links_equal_values(monkeypatch):
    monkeypatch.setattr(correlate, "RULES", load_rules(RULES_PATH))
    items = [
        _item("a", "url", "https://one.example/p", phone_e164="+905321234567"),
        _item("b", "url", "https://two.example/p", phone_e164="+905321234567"),
        _item("c", "url", "https://three.example/p", phone_e164="+905559876543"),
        _item("d", "url", "https://four.example/p", phone_e164="123"),
        _item("e", "url", "https://five.example/p", phone_e164="123"),
    ]
    matches = [m for m in run_rules(items) if m.rule == "same_phone"]
    assert [(m.src, m.dst, m.type, m.weight) for m in matches] == [("a", "b", "same_person", 2.5)]
