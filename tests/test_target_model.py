"""Hedef kimliği DB modeli: handle-only hedefler (nickname + kind).

Spec: `docs/superpowers/specs/2026-11-17-username-first-target-design.md`.
Bir hedef yalnızca kullanıcı adıyla (handle) tanımlanabildiğinde `first_name`/
`last_name` boş kalır; bu yüzden ikisi de nullable olmalı ve handle `nickname`
kolonunda saklanmalı. `kind` ("name" | "handle") NOT NULL ve varsayılanı "name"
olmalı ki mevcut satırlar migration sırasında değer kazansın.

DİKKAT: `showis_contracts.Target` (pydantic) ile `showis.db.models.Target`
(SQLAlchemy) farklı sınıflardır — burada DB modeli sınanır.
"""

from __future__ import annotations

from showis.db.models import Target as DbTarget


def test_target_columns_support_handle_only():
    c = DbTarget.__table__.c
    assert c.nickname.nullable is True
    assert c.first_name.nullable is True and c.last_name.nullable is True
    assert c.kind.nullable is False


def test_target_kind_has_server_default():
    assert DbTarget.__table__.c.kind.server_default.arg == "name"


def test_target_string_widths():
    c = DbTarget.__table__.c
    assert c.nickname.type.length == 128
    assert c.kind.type.length == 16
