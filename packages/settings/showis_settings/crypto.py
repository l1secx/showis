import base64
import logging
import os

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

DEFAULT_DEV_SALT = b"showis_fixed_dev_salt_12345"
#: Geliştirme için sabit anahtar — yalnız açık izinle (SHOWIS_ALLOW_INSECURE_MASTER_KEY=1).
DEV_SEED = b"showis-development-insecure-master-key-32b"
INSECURE_ENV = "SHOWIS_ALLOW_INSECURE_MASTER_KEY"

logger = logging.getLogger("showis.crypto")
_dev_warned = False
#: pytest süreci için tek seferlik, rastgele (statik olmayan) anahtar.
_ephemeral_test_key: bytes | None = None

_TRUTHY = {"1", "true", "yes", "on"}


def _insecure_allowed() -> bool:
    return os.getenv(INSECURE_ENV, "").strip().lower() in _TRUTHY


def _under_pytest() -> bool:
    """pytest, test çalışırken bu env'i set eder (harici bağımlılık yok)."""
    return bool(os.getenv("PYTEST_CURRENT_TEST"))


def _dev_fernet() -> Fernet:
    """MASTER_KEY yokken kullanılacak anahtar; üretimde fail-closed.

    * ``SHOWIS_ALLOW_INSECURE_MASTER_KEY=1`` → bilinen sabit dev anahtarı (açık opt-in),
    * pytest çalışması → süreç başına rastgele anahtar (statik sır yok, testler geçer),
    * aksi halde ``RuntimeError`` (PII/sırlar tahmin edilebilir anahtarla şifrelenmez).
    """
    global _dev_warned, _ephemeral_test_key
    if _insecure_allowed():
        if not _dev_warned:
            logger.warning(
                "%s=1 — sırlar sabit geliştirme anahtarıyla şifreleniyor. "
                "Üretimde MASTER_KEY ayarlayın.",
                INSECURE_ENV,
            )
            _dev_warned = True
        return Fernet(base64.urlsafe_b64encode(DEV_SEED[:32]))
    if _under_pytest():
        if _ephemeral_test_key is None:
            _ephemeral_test_key = Fernet.generate_key()
        return Fernet(_ephemeral_test_key)
    raise RuntimeError(
        "MASTER_KEY tanimli degil. Sirlar fail-closed: MASTER_KEY ayarlayin "
        "(Fermente anahtar: python -c 'from cryptography.fernet import Fernet; "
        "print(Fernet.generate_key().decode())'). Yalniz gelistirme icin "
        f"{INSECURE_ENV}=1 kullanilabilir."
    )


def get_fernet() -> Fernet:
    master_key = os.getenv("MASTER_KEY", "").strip()
    if not master_key:
        return _dev_fernet()
    # Check if already a valid 32-byte urlsafe base64 key
    try:
        return Fernet(master_key.encode("utf-8"))
    except Exception:
        # Derive 32 bytes using PBKDF2
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=DEFAULT_DEV_SALT,
            iterations=100_000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(master_key.encode("utf-8")))
        return Fernet(key)


def encrypt_secret(secret: str) -> bytes:
    if not secret:
        return b""
    f = get_fernet()
    return f.encrypt(secret.encode("utf-8"))


def decrypt_secret(encrypted: bytes | None) -> str:
    if not encrypted:
        return ""
    f = get_fernet()
    return f.decrypt(encrypted).decode("utf-8")


def mask_secret(secret: str) -> str:
    """Örnek: 'ghp_1234567890abcdef' -> 'ghp_****cdef'.

    Kısa sırlarda (≤8) hiçbir karakter gösterilmez; 9-12 arasında yalnız ilk/son
    karakter görünür. Böylece maskeli alan sırrın tamamını geri vermez.
    """
    if not secret:
        return ""
    length = len(secret)
    if length <= 8:
        return "*" * length
    if length <= 12:
        return f"{secret[0]}****{secret[-1]}"
    prefix = secret[:4]
    suffix = secret[-4:]
    return f"{prefix}****{suffix}"
