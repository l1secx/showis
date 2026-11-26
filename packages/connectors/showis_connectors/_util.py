"""Connector'lar arası küçük ortak yardımcılar (kopya kod tekrarını önler)."""

from __future__ import annotations

import re

#: UUID biçimli platform kimlikleri (HTB/THM); kullanıcı adı/etiket sayılmaz.
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


def first_int(pattern: re.Pattern[str], text: str) -> int | None:
    """Regex'in ilk sayısal grubunu int döndürür ("1,234" → 1234)."""
    match = pattern.search(text)
    if not match:
        return None
    for group in match.groups():
        if not group:
            continue
        try:
            return int(str(group).replace(",", ""))
        except ValueError:
            continue
    return None
