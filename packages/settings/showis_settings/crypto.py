import base64
import logging
import os

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

DEFAULT_DEV_SALT = b"showis_fixed_dev_salt_12345"

logger = logging.getLogger("showis.crypto")
_dev_warned = False


def get_fernet() -> Fernet:
    master_key = os.getenv("MASTER_KEY", "").strip()
    if master_key:
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
    else:
        # Development fallback key — MASTER_KEY ayarlanmadan sifrelenen
        # secret'lar herkes tarafindan cozulebilir! Sadece gelistirme icin.
        global _dev_warned
        if not _dev_warned:
            logger.warning(
                "MASTER_KEY tanimli degil — gelistirme anahtari kullaniliyor. "
                "Uretimde MASTER_KEY ayarlayin."
            )
            _dev_warned = True
        dev_seed = b"showis-development-insecure-master-key-32b"
        key = base64.urlsafe_b64encode(dev_seed[:32])
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
    """Örnek: 'ghp_1234567890abcdef' -> 'ghp_****cdef' veya 'sk-****'"""
    if not secret:
        return ""
    length = len(secret)
    if length <= 6:
        return "*" * length
    prefix = secret[:4]
    suffix = secret[-4:]
    return f"{prefix}****{suffix}"
