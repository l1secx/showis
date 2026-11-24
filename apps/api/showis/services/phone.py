"""Telefon numarası normalizasyonu (E.164) ve ülke çıkarımı.

Ağ kullanmaz; yalnız biçim doğrular. Ülke, uluslararası arama kodundan çıkarılır
(numaranın coğrafi konumunu veya sahibinin yerini kanıtlamaz).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Arama kodu → (ISO ülke, Türkçe ad). En uzun önek eşleşir (3, 2, 1 hane).
CALLING_CODES: dict[str, tuple[str, str]] = {
    "1": ("US", "ABD/Kanada"),
    "7": ("RU", "Rusya/Kazakistan"),
    "20": ("EG", "Mısır"),
    "27": ("ZA", "Güney Afrika"),
    "30": ("GR", "Yunanistan"),
    "31": ("NL", "Hollanda"),
    "32": ("BE", "Belçika"),
    "33": ("FR", "Fransa"),
    "34": ("ES", "İspanya"),
    "36": ("HU", "Macaristan"),
    "39": ("IT", "İtalya"),
    "40": ("RO", "Romanya"),
    "41": ("CH", "İsviçre"),
    "43": ("AT", "Avusturya"),
    "44": ("GB", "Birleşik Krallık"),
    "45": ("DK", "Danimarka"),
    "46": ("SE", "İsveç"),
    "47": ("NO", "Norveç"),
    "48": ("PL", "Polonya"),
    "49": ("DE", "Almanya"),
    "52": ("MX", "Meksika"),
    "55": ("BR", "Brezilya"),
    "61": ("AU", "Avustralya"),
    "81": ("JP", "Japonya"),
    "82": ("KR", "Güney Kore"),
    "86": ("CN", "Çin"),
    "90": ("TR", "Türkiye"),
    "91": ("IN", "Hindistan"),
    "98": ("IR", "İran"),
    "351": ("PT", "Portekiz"),
    "353": ("IE", "İrlanda"),
    "357": ("CY", "Kıbrıs"),
    "358": ("FI", "Finlandiya"),
    "374": ("AM", "Ermenistan"),
    "380": ("UA", "Ukrayna"),
    "966": ("SA", "Suudi Arabistan"),
    "971": ("AE", "BAE"),
    "994": ("AZ", "Azerbaycan"),
    "995": ("GE", "Gürcistan"),
}
_ALLOWED = re.compile(r"^\+?[\d\s().\-/]+$")
MIN_DIGITS = 8
MAX_DIGITS = 15  # E.164 üst sınırı


@dataclass(frozen=True)
class PhoneInfo:
    e164: str
    calling_code: str | None
    country: str | None
    country_name: str | None
    national: str


def _split_code(digits: str) -> tuple[str | None, str]:
    for size in (3, 2, 1):
        code = digits[:size]
        if code in CALLING_CODES:
            return code, digits[size:]
    return None, digits


def parse_phone(raw: str, default_calling_code: str = "90") -> PhoneInfo | None:
    """Telefonu E.164'e çevirir; geçersizse `None`.

    `+`/`00` önekli numara olduğu gibi, `0` ile başlayan ulusal numara ve 10 haneli
    çıplak numara `default_calling_code` ülkesine varsayılır.
    """
    text = str(raw or "").strip()
    if not text or not _ALLOWED.match(text):
        return None
    digits = re.sub(r"\D", "", text)
    if text.startswith("+"):
        pass
    elif digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = default_calling_code + digits[1:]
    elif len(digits) == 10:
        digits = default_calling_code + digits
    elif digits.startswith(default_calling_code):
        pass
    else:
        return None
    if not MIN_DIGITS <= len(digits) <= MAX_DIGITS or digits.startswith("0"):
        return None
    code, national = _split_code(digits)
    country, name = CALLING_CODES.get(code or "", (None, None))
    return PhoneInfo(f"+{digits}", code, country, name, national)


def phone_attributes(info: PhoneInfo) -> dict[str, str]:
    attrs = {"phone_e164": info.e164}
    if info.calling_code:
        attrs["phone_calling_code"] = info.calling_code
    if info.country:
        attrs["phone_country"] = info.country
    return attrs


def search_variants(info: PhoneInfo) -> list[str]:
    """Herkese açık web aramasında denenecek biçimler (en belirgin önce)."""
    variants = [info.e164]
    if info.calling_code and info.national:
        spaced = f"+{info.calling_code} {info.national}"
        if info.calling_code == "90" and len(info.national) == 10:
            n = info.national
            variants = [
                info.e164,
                f"+90 {n[:3]} {n[3:6]} {n[6:8]} {n[8:]}",
                f"0{n[:3]} {n[3:6]} {n[6:8]} {n[8:]}",
            ]
        else:
            variants.append(spaced)
    return variants
