import re

# Regex for Turkish and international phone numbers
PHONE_REGEX = re.compile(
    r"(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{2,4}\)?[-.\s]?)?\d{3}[-.\s]?\d{2}[-.\s]?\d{2,4}"
)

# Regex for email addresses
EMAIL_REGEX = re.compile(r"([a-zA-Z0-9_.+-]+)@([a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)")


def mask_email_match(match: re.Match) -> str:
    local_part = match.group(1)
    domain_part = match.group(2)
    if len(local_part) <= 1:
        masked_local = "*"
    else:
        masked_local = f"{local_part[0]}***"
    return f"{masked_local}@{domain_part}"


# API token / gizli anahtar kalıpları (GitHub PAT, OpenAI, generic bearer)
TOKEN_REGEX = re.compile(
    r"\b(ghp|gho|ghu|ghs|ghr|github_pat|sk|xox[baprs]|AKIA|AIza)[-_A-Za-z0-9]{8,}\b"
    r"|\bBearer\s+[A-Za-z0-9._\-]{8,}"
)


def mask_token_match(match: re.Match) -> str:
    return "[TOKEN REDACTED]"


def redact_text(text: str) -> str:
    """Redacts sensitive PII (emails masked, phone numbers redacted) before sending to LLM."""
    if not text:
        return ""
    # Token/API key'leri once maskele (icerik icindeki gizli degerler LLM'e gitmesin)
    redacted = TOKEN_REGEX.sub(mask_token_match, text)
    # Mask emails
    redacted = EMAIL_REGEX.sub(mask_email_match, redacted)

    # Redact phone numbers (minimum 7 digits to avoid matching small numbers)
    def mask_phone_match(match: re.Match) -> str:
        s = str(match.group(0))
        digits = re.sub(r"\D", "", s)
        if len(digits) >= 8:
            return "[TELEFON REDACTED]"
        return s

    return str(PHONE_REGEX.sub(mask_phone_match, redacted))
