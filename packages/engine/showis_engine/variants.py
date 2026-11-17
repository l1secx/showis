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
    nickname: str = "",
    max_variants: int = 60,
) -> VariantSet:
    """İsim ve/veya nick'ten kullanıcı adı varyantları üretir.

    ``nickname`` birinci sınıf argümandır; ``hints`` içinden nick **okunmaz**
    (tek kaynak hedefin kendi alanı). ``hints`` yalnızca yıl/şehir gibi sinyaller
    için kullanılır.
    """
    hints = hints or {}
    ad = normalize_turkish(first_name)
    soyad = normalize_turkish(last_name)
    a = ad[0] if ad else ""

    nick = normalize_turkish(nickname.lstrip("@"))
    year = str(hints.get("year") or hints.get("birth_year") or "")

    variants: list[UsernameVariant] = []
    seen: set[str] = set()

    def add_variant(u: str, pattern: str, confidence: float, min_len: int = 3):
        clean_u = u.strip("._-")
        if clean_u and clean_u not in seen and len(clean_u) >= min_len:
            seen.add(clean_u)
            variants.append(
                UsernameVariant(username=clean_u, pattern=pattern, confidence=confidence)
            )

    # 1. Known handle (exact). Kullanıcının verdiği nick tahmin değil kanıttır:
    #    kısa olsa da (ör. 2 karakter) korunur, bu yüzden min_len=1.
    if nick:
        add_variant(nick, "manual", 1.0, min_len=1)

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

    # 4. Handle tahminleri. Kanıt (exact handle) ve isim desenleri listeye zaten
    #    girdikten sonra eklenir; böylece nick varken bile ilk sıralar
    #    "bilinen handle → isim desenleri" olur.
    if nick:
        if year:
            add_variant(f"{nick}{year}", "nick+year", 0.95)
        add_variant(f"{nick}99", "nick+digits", 0.85)
        add_variant(f"{nick}01", "nick+digits", 0.85)
        add_variant(f"{nick}_dev", "nick_dev", 0.85)
        add_variant(f"{nick}.dev", "nick_dev", 0.85)

    # İsim formları yalnız ad/soyad varsa üretilir; handle-only hedefte boş kalır.
    name_forms: list[str] = []
    if ad or soyad:
        name_forms.append(f"{first_name} {last_name}".strip())
        name_forms.append(f"{ad} {soyad}".strip())
        if ad and soyad:
            name_forms.append(f"{ad}.{soyad}")
            name_forms.append(f"{a}. {last_name}".strip())
        name_forms = [nf for nf in name_forms if nf]

    return VariantSet(
        variants=variants[:max_variants],
        name_forms=name_forms,
    )
