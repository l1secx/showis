"""DB hedef satırı → kontrat `Target` dönüşümü (tek kaynak).

`0007` geçişinde `targets.kind/first_name/last_name/nickname` NULL olabilir;
kontrat bu alanlarda `str` bekler. Dönüşüm tek yerde tutulur ki API okuma
yolları (detail/list/export), tarama tahmini ve scanner **aynı** kimliği görsün
(`guard_target` nick'i de denetler).

Not: bu modül `showis.api`'yi import etmez (API katmanı buradan import eder).
"""

from typing import Any

from showis_contracts import Target


def db_target_to_contract(row: Any) -> Target:
    """DB hedef satırını kontrat `Target`ına çevirir.

    `first_name`/`last_name`/`nickname` için `None → ""`, `kind` için `None`/boş →
    `"name"`. `name_forms`/`hints` varsayılan boş liste/sözlük,
    `review_required` varsayılan `False`'tur.
    """
    return Target(
        kind=row.kind or "name",
        first_name=row.first_name or "",
        last_name=row.last_name or "",
        nickname=row.nickname or "",
        name_forms=getattr(row, "name_forms", None) or [],
        hints=row.hints or {},
        review_required=bool(getattr(row, "review_required", False)),
    )
