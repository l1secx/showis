"""authenticity.parse_timestamp dayanıklılık (P-022)."""

from __future__ import annotations

from showis_engine.authenticity import parse_timestamp


def test_asiri_uzun_dijit_string_taşmaz():
    assert parse_timestamp("9" * 400) is None


def test_normal_epoch_ve_iso_parse_edilir():
    assert parse_timestamp(1700000000) is not None
    assert parse_timestamp("1700000000") is not None
    assert parse_timestamp("2024-01-01T00:00:00Z") is not None
