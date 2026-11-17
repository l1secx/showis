"""DB hedef satırı → kontrat `Target` dönüşümü (tek kaynak).

`0007` geçişinde `targets.kind/first_name/last_name/nickname` NULL olabilir;
kontrat bu alanlarda `str` bekler. Dönüşüm tek yerde tutulur ki API okuma
yolları, tarama tahmini ve scanner **aynı** kimliği görsün (`guard_target`
nick'i de denetler).

Not: bu modül `showis.api`'yi import etmez (API katmanı buradan import eder).
"""

from typing import Any

from showis_contracts import Target


def db_target_to_contract(row: Any, *, default_kind: str = "name") -> Target:
    """DB hedef satırını kontrat `Target`ına çevirir.

    `first_name`/`last_name`/`nickname` için `None → ""`; `kind` için
    `None`/boş → `default_kind`. `hints` varsayılan `{}`, `review_required`
    varsayılan `False`.
    """
    return Target(
        kind=row.kind or default_kind,
        first_name=row.first_name or "",
        last_name=row.last_name or "",
        nickname=row.nickname or "",
        hints=row.hints or {},
        review_required=bool(row.review_required),
    )
