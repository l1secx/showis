"""Site-check (kullanici adi varlik kontrolu) motoru.

WhatsMyName tarzinda calisir: her site icin kamuya acik profil adresi
sablonu tanimlidir; HTTP durumu / JSON alani / HTML isaretleri uzerinden
hesabin var olup olmadigi tespit edilir. Giris duvari, CAPTCHA ve
unofficial API kullanilmaz; robots.txt kurallarina uyulur.
"""

from .cache import CachedCheck, CheckCache, get_default_cache
from .connector import SiteCheckConnector
from .dedupe import ScanPlanRegistry, get_default_registry
from .engine import CheckResult, UsernameCheckEngine
from .search_connector import SiteCheckSearchConnector
from .sites import SITES, SiteSpec

__all__ = [
    "SITES",
    "CachedCheck",
    "CheckCache",
    "CheckResult",
    "ScanPlanRegistry",
    "SiteCheckConnector",
    "SiteCheckSearchConnector",
    "SiteSpec",
    "UsernameCheckEngine",
    "get_default_cache",
    "get_default_registry",
]
