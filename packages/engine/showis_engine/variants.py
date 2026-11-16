import re
from typing import Any

from showis_contracts import UsernameVariant, VariantSet

# Turkish character mapping
TR_MAP = {
    "İ": "i",
    "I": "i",
    "ı": "i",
    "i": "i",
    "Ş": "s",
    "ş": "s",
    "Ğ": "g",
    "ğ": "g",
    "Ü": "u",
    "ü": "u",
    "Ö": "o",
    "ö": "o",
    "Ç": "c",
    "ç": "c",
}


def normalize_turkish(text: str) -> str:
    """Türkçe karakterleri standart latin karakterlere dönüştürür ve küçük harfe çevirir."""
    if not text:
        return ""
    result = []
    for char in text:
        if char in TR_MAP:
            result.append(TR_MAP[char])
        else:
            result.append(char.lower())
    cleaned = "".join(result)
    # Alfanümerik, nokta, alt çizgi, tire haricini temizle
    cleaned = re.sub(r"[^a-z0-9._-]", "", cleaned)
    return cleaned


def generate_variants(
    first_name: str,
    last_name: str,
    hints: dict[str, Any] | None = None,
    max_variants: int = 60,
) -> VariantSet:
    hints = hints or {}
    ad = normalize_turkish(first_name)
    soyad = normalize_turkish(last_name)
    a = ad[0] if ad else ""

    nickname = normalize_turkish(hints.get("nickname") or hints.get("username") or "")
    year = str(hints.get("year") or hints.get("birth_year") or "")

    variants: list[UsernameVariant] = []
    seen: set[str] = set()

    def add_variant(u: str, pattern: str, confidence: float):
        clean_u = u.strip("._-")
        if clean_u and clean_u not in seen and len(clean_u) >= 3:
            seen.add(clean_u)
            variants.append(
                UsernameVariant(username=clean_u, pattern=pattern, confidence=confidence)
            )

    # 1. Manual / Known Nickname
    if nickname:
        add_variant(nickname, "manual", 1.0)
        if year:
            add_variant(f"{nickname}{year}", "nick+year", 0.95)
        add_variant(f"{nickname}99", "nick+digits", 0.85)
        add_variant(f"{nickname}01", "nick+digits", 0.85)
        add_variant(f"{nickname}_dev", "nick_dev", 0.85)
        add_variant(f"{nickname}.dev", "nick_dev", 0.85)

    # 2. Primary name-surname patterns (High confidence)
    if ad and soyad:
        add_variant(f"{ad}.{soyad}", "first.last", 0.95)
        add_variant(f"{ad}{soyad}", "firstlast", 0.90)
        add_variant(f"{ad}_{soyad}", "first_last", 0.88)
        add_variant(f"{ad}-{soyad}", "first-last", 0.85)
        add_variant(f"{a}.{soyad}", "f.last", 0.85)
        add_variant(f"{a}{soyad}", "flast", 0.85)
        add_variant(f"{soyad}.{ad}", "last.first", 0.80)
        add_variant(f"{soyad}{ad}", "lastfirst", 0.75)
        add_variant(f"{ad}.{soyad}.dev", "first.last.dev", 0.75)
        add_variant(f"{ad}{soyad}.dev", "firstlast.dev", 0.75)

        # With year/digits
        if year:
            add_variant(f"{ad}{soyad}{year}", "firstlast+year", 0.80)
            add_variant(f"{ad}.{soyad}{year}", "first.last+year", 0.80)
            add_variant(f"{a}{soyad}{year}", "flast+year", 0.75)

        add_variant(f"{ad}{soyad}99", "firstlast+99", 0.70)
        add_variant(f"{ad}{soyad}01", "firstlast+01", 0.70)
        add_variant(f"{ad}_{soyad}_dev", "first_last_dev", 0.70)

    # 3. First name / Last name standalone
    if ad:
        add_variant(ad, "first", 0.60)
        if year:
            add_variant(f"{ad}{year}", "first+year", 0.65)
        add_variant(f"{ad}_dev", "first_dev", 0.60)
        add_variant(f"{ad}dev", "firstdev", 0.60)

    if soyad:
        add_variant(soyad, "last", 0.60)
        if year:
            add_variant(f"{soyad}{year}", "last+year", 0.65)

    name_forms = [
        f"{first_name} {last_name}".strip(),
        f"{ad} {soyad}".strip(),
        f"{ad}.{soyad}",
        f"{a}. {last_name}".strip(),
    ]

    return VariantSet(
        variants=variants[:max_variants],
        name_forms=[nf for nf in name_forms if nf],
    )
