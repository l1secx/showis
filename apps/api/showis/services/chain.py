"""Kimlik zinciri motoru: onaylı bulgulardan güven yayılımlı genişletme.

Analistin onayladığı (``confirmed``) havuz kayıtları, seed'ler ve hedef
alanları **çapa**dır (güven 1.0). Motor her çapadan sahibin kendi yazdığı
bağlantıları izler ve yeni hesapları bulur:

* GitHub ``blog``/``twitter_username``/sosyal hesaplar/profil README'si,
  commit e-postaları; GitLab, dev.to, Keybase kanıtları, Bluesky alan adı,
  Hacker News "about", Gravatar doğrulanmış hesapları
  (``showis_connectors.account_intel``),
* kişisel site/toplayıcı (linktree vb.) sayfalarındaki ``rel="me"`` ve
  profil bağlantıları.

Güven modeli (deterministik, LLM yok):

* Bir kenarın ağırlığı kanıt türüne bağlıdır (``EDGE_WEIGHTS``); çocuk güveni
  ``ebeveyn güveni × kenar ağırlığı`` olur.
* Aynı düğüme bağımsız yollardan ulaşılırsa güvenler gürültülü-VEYA ile
  birleşir: ``1 - Π(1 - p_i)``.
* **Geri bağlantı**: keşfedilen hesap, zaten güvenilir bir düğüme geri
  bağlanıyorsa (karşılıklı bağlantı) güçlü bağımsız kanıt eklenir.
* İsim tutarlılığı: profildeki görünen ad bilinen isimle eşleşirse kanıt
  eklenir; açıkça farklıysa yalnız kullanıcı adı eşleşmesine dayanan düğüm
  cezalandırılır.

Yalnız kullanıcı adı aynı diye başka platformda bulunan hesap (tahmin) düşük
güvenle başlar ve genişletilmez; ancak geri bağlantı veya isim eşleşmesiyle
yükselirse zincire katılır. Böylece yanlış pozitifler zincir boyunca çoğalmaz.

Çıktı havuza yalnız ``proposed``/``candidate`` olarak yazılır (ürün
invariant'ı); ``confirmed`` yalnız kullanıcı kararıdır.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Sequence
from urllib.parse import parse_qs, urlparse

from showis.db.models import Case, PoolItem, Seed, Target
from showis.services.pool import PoolValidationError, create_item
from showis.services.reasoning.graph import RelationError, upsert_relation
from showis.services.research_playbook import (
    ProfileRef,
    aggregator_domain,
    canonical_profile,
    clean_handle,
    host_of,
)
from showis_connectors.account_intel import (
    HANDLE_FETCHERS,
    KEYBASE_SERVICES,
    AccountIntel,
    AccountIntelError,
    fetch_gravatar,
    keybase_reverse,
    normalize_link,
)
from showis_engine.http import HttpClient
from showis_engine.variants import normalize_turkish
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

#: Kanıt türü -> kenar ağırlığı (çocuğun ebeveynden devraldığı güven oranı).
EDGE_WEIGHTS: dict[str, float] = {
    "proof": 0.99,  # Keybase/Gravatar doğrulanmış, Bluesky alan adı
    "owner": 0.95,  # sahibin profil alanına yazdığı bağlantı
    "rel_me": 0.95,  # sayfa sahibinin rel="me" beyanı
    "readme": 0.9,  # sahibin kendi profil README'si
    "aggregator": 0.92,  # sahibin derlediği linktree/beacons sayfası
    "commit": 0.85,  # kendi push olaylarındaki commit e-postası
    "bio": 0.85,  # biyografi metninde geçen bağlantı
    "site": 0.8,  # kişisel sitedeki (rel="me" olmayan) profil bağlantısı
    "handle_probe": 0.5,  # yalnız aynı kullanıcı adı (tahmin)
}
#: Sahip zincirinden gelmeyen kanıtlar: düğüm doğrulanmak için okunur ama
#: kendi bağlantıları ancak güven eşiği aşılınca izlenir.
_UNVERIFIED_RELATIONS: frozenset[str] = frozenset({"handle_probe", "search", "name"})
#: Geri bağlantı (karşılıklı bağlantı) ve isim eşleşmesi kanıt güçleri.
BACKLINK_WEIGHT = 0.9
NAME_MATCH_WEIGHT = 0.7
NAME_MISMATCH_FACTOR = 0.5
#: Kurum hesabına ulaşan zincir: kişinin hesabı değil, bağlam bilgisidir.
ORG_FACTOR = 0.3
#: Kişisel sayfada bundan fazla profil bağlantısı varsa (blog bahsetmeleri)
#: yalnız bilinen kullanıcı adı/isimle örtüşenler izlenir.
SITE_LINK_FLOOD = 10
#: Hesabın kurum/konum alanları bu güvenin üstünde bağlam kaydı olur.
CONTEXT_MIN_TRUST = 0.9
#: Bu güvenin altındaki düğümler genişletilmez (zincir yanlış pozitifle büyümez).
EXPAND_MIN_TRUST = 0.6
#: Bu güvenin altındaki düğümler havuza yazılmaz.
PERSIST_MIN_TRUST = 0.4
#: Geri bağlantı kanıtı sayılabilmesi için hedef düğümün güveni.
BACKLINK_MIN_TRUST = 0.85
#: İki hesabı `same_person` ilişkisiyle bağlamak için en düşük güven.
SAME_PERSON_MIN_TRUST = 0.85
MAX_CHILDREN = 25
DEFAULT_MAX_FETCHES = 30
DEFAULT_MAX_DEPTH = 3

#: Bilinen kullanıcı adı ile başka platformlarda denenen API'ler.
PROBE_PLATFORMS: tuple[str, ...] = ("github", "gitlab", "keybase", "devto", "codeberg")

#: Giriş duvarı olan veya içeriği istemci tarafında çizilen ağlar: sayfası
#: okunmaz, düğüm yaprak kalır (bağlantı kanıtı yine de geçerlidir).
_WALLED_NETWORKS: frozenset[str] = frozenset(
    {"x", "instagram", "facebook", "linkedin", "tiktok", "threads", "snapchat", "discord"}
)
#: Sayfası okunabilen profil platformları (araştırma ajanıyla aynı küme).
_FETCHABLE_NETWORKS: frozenset[str] = frozenset(
    {
        "linktr.ee",
        "about.me",
        "beacons",
        "carrd",
        "reddit",
        "medium",
        "wordpress",
        "substack",
        "huggingface",
        "steam",
        "soundcloud",
        "youtube",
        "codepen",
        "behance",
        "dribbble",
        "itch.io",
        "hashnode",
    }
)
#: Kişisel site sayılmayan, kullanıcıya ait olmayan genel barındırma kökleri.
_SHARED_HOSTS: frozenset[str] = frozenset(
    {
        "github.com",
        "gitlab.com",
        "google.com",
        "youtube.com",
        "youtu.be",
        "wikipedia.org",
        "stackoverflow.com",
        "npmjs.com",
        "pypi.org",
        "docs.github.com",
        "gist.github.com",
        "t.co",
        "bit.ly",
    }
)
_SOCIAL_HOST_NETWORK: dict[str, str] = {
    "x.com": "x",
    "twitter.com": "x",
    "facebook.com": "facebook",
    "linkedin.com": "linkedin",
    "threads.net": "threads",
    "threads.com": "threads",
    "snapchat.com": "snapchat",
    "discord.gg": "discord",
}


@dataclass
class PageData:
    """Zincir motorunun bir sayfadan ihtiyaç duyduğu alanlar."""

    url: str
    title: str = ""
    links: list[str] = field(default_factory=list)
    rel_me: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)


IntelFetcher = Callable[[str, str], Awaitable[AccountIntel | None]]
PageFetcher = Callable[[str, bool], Awaitable[PageData | None]]


@dataclass
class Evidence:
    relation: str
    weight: float
    source_key: str
    source_label: str
    source_url: str
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "relation": self.relation,
            "weight": round(self.weight, 4),
            "from": self.source_label,
            "source_url": self.source_url,
            "note": self.note,
        }


@dataclass
class ChainNode:
    key: str
    kind: str  # profile | page | email
    value: str
    network: str = ""
    url: str = ""
    depth: int = 0
    anchor: bool = False
    anchor_item_id: str | None = None
    #: Çapa güveni: onaylı kayıt 1.0; analistin seçtiği onaysız kayıt kendi güveni.
    anchor_trust: float = 1.0
    evidence: list[Evidence] = field(default_factory=list)
    trust: float = 0.0
    parent_key: str | None = None
    expanded: bool = False
    display_name: str = ""
    location: str = ""
    company: str = ""
    #: Kurum (organizasyon) hesabı: kişi değil, zincirde ilerlenmez.
    is_org: bool = False
    avatar_url: str = ""
    name_match: bool | None = None

    @property
    def label(self) -> str:
        if self.kind == "profile":
            return f"{self.network}:{self.value}"
        return self.value

    def recompute(self) -> None:
        if self.anchor:
            self.trust = self.anchor_trust
            return
        missing = 1.0
        best: dict[tuple[str, str], float] = {}
        for item in self.evidence:
            key = (item.relation, item.source_key)
            best[key] = max(best.get(key, 0.0), item.weight)
        for weight in best.values():
            missing *= 1.0 - max(0.0, min(0.999, weight))
        trust = 1.0 - missing
        if self.name_match is False and self._unverified():
            trust *= NAME_MISMATCH_FACTOR
        if self.is_org:
            trust *= ORG_FACTOR
        self.trust = round(min(trust, 0.995), 4)

    def _probe_only(self) -> bool:
        """Yalnız kullanıcı adı tahmini (sahip bağlantısı/arama kanıtı yok)."""
        return all(item.relation in {"handle_probe", "name"} for item in self.evidence)

    def _unverified(self) -> bool:
        """Tahmin veya arama sonucu: sahip zincirinden gelen kanıt yok."""
        return all(item.relation in _UNVERIFIED_RELATIONS for item in self.evidence)

    def independent_sources(self) -> int:
        return len({item.source_key for item in self.evidence if item.weight > 0})

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "kind": self.kind,
            "value": self.value,
            "network": self.network,
            "url": self.url,
            "depth": self.depth,
            "anchor": self.anchor,
            "trust": self.trust,
            "evidence": [item.to_dict() for item in self.evidence[:12]],
            "display_name": self.display_name,
        }


@dataclass
class ChainResult:
    nodes: list[ChainNode] = field(default_factory=list)
    fetches: int = 0
    notes: list[str] = field(default_factory=list)
    created_item_ids: list[str] = field(default_factory=list)
    updated_item_ids: list[str] = field(default_factory=list)
    relations: int = 0

    def discovered(self, *, min_trust: float = PERSIST_MIN_TRUST) -> list[ChainNode]:
        # Yalnız kullanıcı adı aynı olan (isimle de doğrulanmamış) tahminler
        # öneri değildir; havuzu gürültüyle doldurmaz.
        return [
            n
            for n in self.nodes
            if not n.anchor
            and n.trust >= min_trust
            and not (n._probe_only() and n.name_match is not True)
        ]

    def summary(self) -> dict[str, Any]:
        found = sorted(self.discovered(), key=lambda n: -n.trust)
        return {
            "anchors": sum(1 for n in self.nodes if n.anchor),
            "discovered": len(found),
            "high_confidence": sum(1 for n in found if n.trust >= 0.85),
            "fetches": self.fetches,
            "created": len(self.created_item_ids),
            "updated": len(self.updated_item_ids),
            "relations": self.relations,
            "nodes": [n.to_dict() for n in found[:60]],
            "notes": self.notes[-20:],
        }

    def context_text(self, limit: int = 30) -> str:
        """LLM bağlamı için kısa zincir özeti (güven sıralı)."""
        lines: list[str] = []
        by_key = {n.key: n for n in self.nodes}
        for node in sorted(self.discovered(), key=lambda n: -n.trust)[:limit]:
            path = [node.label]
            cursor = node.parent_key
            guard = 0
            while cursor and cursor in by_key and guard < 6:
                path.append(by_key[cursor].label)
                cursor = by_key[cursor].parent_key
                guard += 1
            relations = ",".join(sorted({e.relation for e in node.evidence}))
            lines.append(
                f"- {node.label} güven={node.trust:.2f} kanıt={relations} "
                f"zincir={' <- '.join(path)}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------- kimlik


def _fold(text: str) -> str:
    # normalize_turkish boşlukları da siler; parça parça uygulanır.
    words = str(text or "").replace("-", " ").replace("_", " ").split()
    return " ".join(normalize_turkish(word).casefold() for word in words if word)


def _name_tokens(text: str) -> set[str]:
    return {token for token in _fold(text).split() if len(token) >= 3}


def profile_identity(url: str) -> tuple[str, str, str] | None:
    """URL -> (ağ, kimlik, kanonik URL); profil değilse None."""
    parsed = urlparse(url)
    if host_of(url) == "news.ycombinator.com" and parsed.path.rstrip("/") == "/user":
        ident = parse_qs(parsed.query).get("id", [""])[0].strip()
        if ident and clean_handle(ident) == ident.lower():
            return "hackernews", ident.lower(), f"https://news.ycombinator.com/user?id={ident}"
        return None
    ref: ProfileRef | None = canonical_profile(url)
    if ref is None:
        # Fediverse (Mastodon uyumlu): bilinmeyen sunucuda tek parçalı /@kullanici.
        match = _FEDI_PATH_RE.match(parsed.path)
        host = host_of(url)
        if match and host and "." in host and not parsed.query:
            user = match.group(1).lower()
            return "mastodon", f"{user}@{host}", f"https://{host}/@{match.group(1)}"
        return None
    network = ref.platform.value
    if network == "mastodon":
        return (
            "mastodon",
            f"{ref.identifier.lower()}@{host_of(url)}",
            url.split("#")[0],
        )
    if network == "web":
        host = host_of(url)
        if host.endswith(".carrd.co"):
            network = "carrd"
        else:
            network = _SOCIAL_HOST_NETWORK.get(host) or aggregator_domain(url) or host
    if network == "linktree.com":
        network = "linktr.ee"
    return network, ref.identifier.lower(), url.split("#")[0]


_FEDI_PATH_RE = re.compile(r"^/@([A-Za-z0-9_]{1,30})/?$")


def _page_key(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower().removeprefix("www.")
    path = parsed.path.rstrip("/")
    return f"page:{host}{path}"


def _brand(host: str) -> str:
    """Kayıtlı alan adının marka etiketi: tangled.org -> tangled, x.co.uk -> x."""
    parts = [part for part in host.lower().removeprefix("www.").split(".") if part]
    if len(parts) < 2:
        return host.lower()
    if len(parts) >= 3 and parts[-2] in {"co", "com", "org", "net", "gov", "edu", "ac"}:
        return parts[-3]
    return parts[-2]


def _is_personal_site(url: str) -> bool:
    host = host_of(url)
    if not host or "." not in host:
        return False
    if any(host == shared or host.endswith(f".{shared}") for shared in _SHARED_HOSTS):
        return False
    return True


# ---------------------------------------------------------------- motor


class ChainEngine:
    """Çapalardan genişleyen, bütçeli ve deterministik kimlik zinciri."""

    def __init__(
        self,
        *,
        intel_fetcher: IntelFetcher,
        page_fetcher: PageFetcher,
        email_fetcher: Callable[[str], Awaitable[AccountIntel | None]] | None = None,
        max_fetches: int = DEFAULT_MAX_FETCHES,
        max_depth: int = DEFAULT_MAX_DEPTH,
        names: Iterable[str] = (),
        probe_handles: bool = True,
        reverse_fetcher: Callable[[str, str], Awaitable[list[str]]] | None = None,
    ):
        self.intel_fetcher = intel_fetcher
        self.reverse_fetcher = reverse_fetcher
        self.page_fetcher = page_fetcher
        self.email_fetcher = email_fetcher
        self.max_fetches = max(0, int(max_fetches))
        self.max_depth = max(0, int(max_depth))
        self.probe_handles = probe_handles
        self.nodes: dict[str, ChainNode] = {}
        self.names: set[str] = set()
        self.name_tokens: set[str] = set()
        for name in names:
            self.add_name(name)
        self.result = ChainResult()

    # -------------------------------------------------------- kayıt

    def add_name(self, name: str) -> None:
        tokens = _name_tokens(name)
        if len(tokens) >= 1:
            self.names.add(_fold(name))
            self.name_tokens |= tokens

    def known_handles(self) -> set[str]:
        return {
            node.value.lower()
            for node in self.nodes.values()
            if node.kind == "profile" and node.trust >= BACKLINK_MIN_TRUST
        }

    def _node(self, key: str, **kwargs: Any) -> tuple[ChainNode, bool]:
        existing = self.nodes.get(key)
        if existing is not None:
            return existing, False
        node = ChainNode(key=key, **kwargs)
        self.nodes[key] = node
        return node, True

    def add_anchor_url(
        self, url: str, *, item_id: str | None = None, trust: float = 1.0
    ) -> ChainNode | None:
        clean = normalize_link(url)
        if not clean:
            return None
        identity = profile_identity(clean)
        if identity is not None:
            network, ident, canonical = identity
            node, _ = self._node(
                f"profile:{network}:{ident}",
                kind="profile",
                value=ident,
                network=network,
                url=canonical,
            )
        else:
            node, _ = self._node(_page_key(clean), kind="page", value=clean, url=clean)
        self._mark_anchor(node, item_id, trust)
        return node

    def add_anchor_profile(
        self,
        network: str,
        handle: str,
        *,
        url: str = "",
        item_id: str | None = None,
        trust: float = 1.0,
    ) -> ChainNode | None:
        if network in {"bluesky", "mastodon"}:
            ident = handle.strip().lstrip("@").lower()
        else:
            ident = clean_handle(handle)
        if not ident:
            return None
        node, _ = self._node(
            f"profile:{network}:{ident}",
            kind="profile",
            value=ident,
            network=network,
            url=url,
        )
        self._mark_anchor(node, item_id, trust)
        return node

    def add_anchor_email(
        self, email: str, *, item_id: str | None = None, trust: float = 1.0
    ) -> ChainNode | None:
        address = str(email or "").strip().lower()
        if "@" not in address:
            return None
        node, _ = self._node(f"email:{address}", kind="email", value=address)
        self._mark_anchor(node, item_id, trust)
        return node

    def add_anchor_handle(self, handle: str) -> None:
        """Platformu bilinmeyen onaylı kullanıcı adı: API'lerde aynı adı dene."""
        text = str(handle or "").strip().lstrip("@")
        if "@" in text:  # fediverse: kullanici@sunucu -> kullanici
            text = text.split("@", 1)[0]
        elif text.endswith(".bsky.social"):
            text = text.removesuffix(".bsky.social")
        elif "." in text and not text.replace(".", "").isalnum():
            return
        ident = clean_handle(text)
        if not ident or not self.probe_handles:
            return
        source_key = f"handle:{ident}"
        for network in PROBE_PLATFORMS:
            key = f"profile:{network}:{ident}"
            node, _ = self._node(key, kind="profile", value=ident, network=network)
            if node.anchor:
                continue
            self._add_evidence(
                node,
                Evidence(
                    relation="handle_probe",
                    weight=EDGE_WEIGHTS["handle_probe"],
                    source_key=source_key,
                    source_label=f"onaylı kullanıcı adı {ident}",
                    source_url="",
                    note="aynı kullanıcı adı; doğrulama bekliyor",
                ),
            )

    def add_lead(self, url: str, weight: float, *, note: str = "") -> ChainNode | None:
        """Araştırmanın arama/sayfa ile bulduğu (onaysız) profil: doğrulanmak üzere.

        Çapa değildir; güveni arama kanıtı ağırlığıyla başlar. Geri bağlantı
        veya isim eşleşmesi bulunursa yükselir ve havuzdaki güven güncellenir.
        """
        clean = normalize_link(url)
        identity = profile_identity(clean) if clean else None
        if identity is None:
            return None
        network, ident, canonical = identity
        key = f"profile:{network}:{ident}"
        node, created = self._node(
            key, kind="profile", value=ident, network=network, url=canonical
        )
        if node.anchor:
            return node
        if created:
            node.depth = 1
        self._add_evidence(
            node,
            Evidence(
                relation="search",
                weight=max(0.0, min(0.8, weight)),
                source_key=f"search:{node.key}",
                source_label="araştırma bulgusu",
                source_url=canonical,
                note=note[:200],
            ),
        )
        return node

    def _mark_anchor(self, node: ChainNode, item_id: str | None, trust: float = 1.0) -> None:
        # Aynı düğüm hem onaylı hem onaysız çapa olursa ya da zaten zincirle
        # doğrulanmışsa güçlü olan kalır.
        node.anchor_trust = max(trust, node.trust)
        node.anchor = True
        node.depth = 0
        node.anchor_item_id = item_id or node.anchor_item_id
        node.recompute()

    def _add_evidence(self, node: ChainNode, evidence: Evidence) -> None:
        if any(
            e.relation == evidence.relation and e.source_key == evidence.source_key
            for e in node.evidence
        ):
            return
        node.evidence.append(evidence)
        node.recompute()

    # -------------------------------------------------------- bağlantı

    def link(
        self,
        parent: ChainNode,
        url: str,
        relation: str,
        *,
        label: str = "",
        source_url: str = "",
    ) -> ChainNode | None:
        """Ebeveynden URL'ye kenar ekler; uygun değilse None."""
        clean = normalize_link(url)
        if not clean:
            return None
        weight = EDGE_WEIGHTS.get(relation, 0.5)
        identity = profile_identity(clean)
        if identity is not None:
            network, ident, canonical = identity
            if parent.kind == "profile" and network == parent.network:
                if ident == parent.value:
                    return None
                # Aynı platformda başka hesap (takip edilen, ortak çalışan)
                # sahibin kendisi değildir; README/bio/site bağlantısında atla.
                if relation in {"readme", "bio", "site"}:
                    return None
            if relation in {"readme", "bio", "site"} and ident in self.known_handles():
                weight = max(weight, EDGE_WEIGHTS["owner"])
            key = f"profile:{network}:{ident}"
            node, created = self._node(
                key, kind="profile", value=ident, network=network, url=canonical
            )
        else:
            if relation in {"readme", "bio", "site"} and not self._plausible_site(clean):
                return None
            if not _is_personal_site(clean):
                return None
            node, created = self._node(_page_key(clean), kind="page", value=clean, url=clean)
        if node is parent:
            return None
        if created:
            node.depth = parent.depth + 1
            node.parent_key = parent.key
        else:
            node.depth = min(node.depth, parent.depth + 1)
            if node.parent_key is None and not node.anchor:
                node.parent_key = parent.key
        if not node.url:
            node.url = clean
        self._add_evidence(
            node,
            Evidence(
                relation=relation,
                weight=parent.trust * weight,
                source_key=parent.key,
                source_label=parent.label,
                source_url=source_url or parent.url,
                note=label,
            ),
        )
        return node

    def _plausible_site(self, url: str) -> bool:
        """README/bio'daki profil dışı URL ancak kimliğe benziyorsa site sayılır."""
        host = host_of(url)
        folded = host.replace("-", "").replace(".", " ")
        for handle in self.known_handles() | {n.value for n in self.nodes.values() if n.anchor}:
            if handle and len(handle) >= 3 and handle.replace("-", "") in folded.replace(" ", ""):
                return True
        return any(token in folded for token in self.name_tokens if len(token) >= 4)

    def add_email(self, parent: ChainNode, email: str, relation: str, *, note: str = "") -> None:
        address = str(email or "").strip().lower()
        if "@" not in address:
            return
        node, created = self._node(f"email:{address}", kind="email", value=address)
        if created:
            node.depth = parent.depth + 1
            node.parent_key = parent.key
        self._add_evidence(
            node,
            Evidence(
                relation=relation,
                weight=parent.trust * EDGE_WEIGHTS.get(relation, 0.8),
                source_key=parent.key,
                source_label=parent.label,
                source_url=parent.url,
                note=note,
            ),
        )

    def _check_backlinks(self, node: ChainNode, urls: Iterable[str]) -> None:
        """Düğümün sayfası güvenilir bir düğüme geri bağlanıyorsa kanıt ekler."""
        for url in urls:
            clean = normalize_link(url)
            if not clean:
                continue
            identity = profile_identity(clean)
            key = (
                f"profile:{identity[0]}:{identity[1]}" if identity is not None else _page_key(clean)
            )
            target = self.nodes.get(key)
            if target is None or target is node or target.trust < BACKLINK_MIN_TRUST:
                continue
            if any(e.relation == "backlink" and e.source_key == target.key for e in node.evidence):
                continue
            self._add_evidence(
                node,
                Evidence(
                    relation="backlink",
                    weight=target.trust * BACKLINK_WEIGHT,
                    source_key=target.key,
                    source_label=target.label,
                    source_url=node.url,
                    note=f"{node.label} sayfası {target.label} hesabına geri bağlanıyor",
                ),
            )

    def _check_name(self, node: ChainNode, display_name: str) -> None:
        if not display_name or not self.name_tokens:
            return
        tokens = _name_tokens(display_name)
        if not tokens:
            return
        overlap = tokens & self.name_tokens
        # Tek ortak ad (ör. yalnız "Ali") yetmez: en az iki parça örtüşmeli ya da
        # görünen ad bilinen tam isimle birebir aynı olmalı. Tek parçalı ve
        # örtüşen ad (ör. "dan") nötrdür; ad aynı soyad farklıysa uyuşmazlıktır.
        if len(overlap) >= 2 or _fold(display_name) in self.names:
            node.name_match = True
            self._add_evidence(
                node,
                Evidence(
                    relation="name",
                    weight=NAME_MATCH_WEIGHT,
                    source_key=f"name:{node.key}",
                    source_label="görünen ad",
                    source_url=node.url,
                    note=f"görünen ad '{display_name[:80]}' bilinen isimle eşleşiyor",
                ),
            )
        elif overlap and len(tokens) == 1:
            return
        elif self._is_handle_like(node, display_name):
            # Görünen ad kullanıcı adının kendisi (ör. "Gargron"): bilgi taşımaz.
            return
        else:
            node.name_match = False
            node.recompute()

    # -------------------------------------------------------- genişletme

    def extend_budget(self, fetches: int) -> None:
        """Aynı motorla sonraki aşamaya istek hakkı ekler (okunanlar tekrar okunmaz)."""
        self.max_fetches += max(0, int(fetches))

    def _can_fetch(self) -> bool:
        return self.result.fetches < self.max_fetches

    async def _expand(self, node: ChainNode) -> None:
        node.expanded = True
        if node.kind == "profile":
            if node.network in HANDLE_FETCHERS:
                await self._expand_intel(node)
            elif node.network in _WALLED_NETWORKS:
                pass
            elif node.network in _FETCHABLE_NETWORKS or aggregator_domain(node.url):
                await self._expand_page(node, aggregator=bool(aggregator_domain(node.url)))
            service = KEYBASE_SERVICES.get(node.network)
            if service:
                await self._reverse_keybase(node, service, node.value)
        elif node.kind == "page":
            await self._expand_page(node, aggregator=bool(aggregator_domain(node.url)))
            if self.is_own_site(node):
                await self._reverse_keybase(node, "domain", host_of(node.url))
        elif node.kind == "email" and self.email_fetcher is not None:
            await self._expand_email(node)

    async def _expand_intel(self, node: ChainNode) -> None:
        if not self._can_fetch():
            return
        self.result.fetches += 1
        handle = node.value
        if node.network == "hackernews" and node.url:
            # HN kullanıcı adları büyük/küçük harf duyarlıdır; özgün yazım URL'de.
            handle = parse_qs(urlparse(node.url).query).get("id", [handle])[0]
        try:
            intel = await self.intel_fetcher(node.network, handle)
        except AccountIntelError as exc:
            self.result.notes.append(f"{node.label}: {exc}")
            return
        except Exception as exc:  # noqa: BLE001 - tek uç zinciri düşürmesin
            self.result.notes.append(f"{node.label}: {type(exc).__name__}")
            return
        if intel is None:
            if not node.anchor and node._unverified():
                # Olmayan hesap: tahmin/arama düğümü tamamen düşer.
                node.evidence.clear()
                node.trust = 0.0
            self.result.notes.append(f"{node.label}: hesap bulunamadı")
            return
        self._apply_intel(node, intel)

    def _apply_intel(self, node: ChainNode, intel: AccountIntel) -> None:
        node.url = intel.profile_url or node.url
        if intel.account_type == "organization" and not node.anchor:
            # Kurum hesabı kişinin kendisi değildir: güveni düşer, genişletilmez.
            node.is_org = True
            node.recompute()
            self.result.notes.append(f"{node.label}: kurum hesabı, zincir burada durur")
            return
        node.display_name = intel.display_name
        node.location = intel.location
        node.company = intel.company
        node.avatar_url = intel.avatar_url
        if node.anchor and intel.display_name:
            self.add_name(intel.display_name)
        self._check_name(node, intel.display_name)
        self._check_backlinks(node, [link.url for link in intel.links])
        if not node.anchor and node.trust >= 0.9 and intel.display_name:
            self.add_name(intel.display_name)
        if node.trust < EXPAND_MIN_TRUST:
            return
        for link in intel.links[:MAX_CHILDREN]:
            self.link(node, link.url, link.relation, label=link.label, source_url=node.url)
        for email, origin in intel.emails:
            relation = "commit" if origin == "github-commit" else "owner"
            self.add_email(node, email, relation, note=origin)
        for network, handle, relation in intel.handles:
            child, created = self._node(
                f"profile:{network}:{handle.lower()}",
                kind="profile",
                value=handle.lower(),
                network=network,
            )
            if created:
                child.depth = node.depth + 1
                child.parent_key = node.key
            self._add_evidence(
                child,
                Evidence(
                    relation=relation,
                    weight=node.trust * EDGE_WEIGHTS.get(relation, 0.8),
                    source_key=node.key,
                    source_label=node.label,
                    source_url=node.url,
                ),
            )

    async def _expand_page(self, node: ChainNode, *, aggregator: bool) -> None:
        if not self._can_fetch():
            return
        self.result.fetches += 1
        try:
            page = await self.page_fetcher(node.url, node.anchor)
        except Exception as exc:  # noqa: BLE001
            self.result.notes.append(f"{node.label}: {type(exc).__name__}")
            return
        if page is None:
            self.result.notes.append(f"{node.label}: sayfa okunamadı")
            return
        self._check_backlinks(node, [*page.rel_me, *page.links])
        if node.trust < EXPAND_MIN_TRUST:
            return
        rel_me = set(page.rel_me)
        for url in page.rel_me[:MAX_CHILDREN]:
            self.link(node, url, "rel_me", label="rel=me", source_url=node.url)
        relation = "aggregator" if aggregator else "site"
        # Platformda barındırılan profil sayfası (tangled.org/ali, medium.com/@ali):
        # gövdedeki platformun kendi hesapları/e-postaları (alt bilgi) sahibin değildir.
        brand = "" if aggregator or self.is_own_site(node) else _brand(host_of(node.url))
        flood = not aggregator and (
            len({profile_identity(url) for url in page.links} - {None}) > SITE_LINK_FLOOD
        )
        added = 0
        for url in page.links:
            if added >= MAX_CHILDREN or url in rel_me:
                continue
            identity = profile_identity(url)
            if identity is None and not aggregator:
                # Kişisel sitedeki profil dışı bağlantılar (blog yazıları) zincir değildir.
                continue
            if brand and (
                _brand(host_of(url)) == brand or (identity is not None and brand in identity[1])
            ):
                continue
            if flood and (identity is None or not self._resembles_identity(identity[1])):
                continue
            if self.link(node, url, relation, source_url=node.url) is not None:
                added += 1
        for email in page.emails[:4]:
            if brand and _brand(email.rpartition("@")[2]) == brand:
                continue
            self.add_email(node, email, "owner" if aggregator else "site", note="sayfada")

    def _is_handle_like(self, node: ChainNode, display_name: str) -> bool:
        flat = _fold(display_name).replace(" ", "")
        handles = {node.value.split("@", 1)[0]} | self.known_handles()
        return any(flat == h.split("@", 1)[0].replace("-", "").replace("_", "") for h in handles)

    def _resembles_identity(self, ident: str) -> bool:
        """Kimlik bilinen bir kullanıcı adı veya isim parçasıyla örtüşüyor mu?"""
        flat = ident.lower().split("@", 1)[0].replace("-", "").replace("_", "").replace(".", "")
        handles = self.known_handles() | {n.value for n in self.nodes.values() if n.anchor}
        for handle in handles:
            base = handle.split("@", 1)[0].replace("-", "").replace("_", "").replace(".", "")
            if len(base) >= 3 and (base in flat or flat in base):
                return True
        return any(len(token) >= 4 and token in flat for token in self.name_tokens)

    def is_own_site(self, node: ChainNode) -> bool:
        """Kök yolda kişisel alan adı mı (ali.dev), yoksa platformdaki bir sayfa mı?"""
        if node.kind != "page":
            return False
        path = urlparse(node.url).path.strip("/")
        return not path or self._plausible_site(node.url)

    async def _reverse_keybase(self, node: ChainNode, service: str, name: str) -> None:
        """Bu hesabı/alan adını kriptografik olarak kanıtlamış Keybase kullanıcısı.

        Keybase kanıtı sahibin imzasıdır: bulunan Keybase hesabı aynı kişidir.
        Giriş duvarlı ağlar (X) dahil; tek istekle ters yönde doğrulama yapar.
        """
        if (
            self.reverse_fetcher is None
            or node.trust < BACKLINK_MIN_TRUST
            or not self._can_fetch()
        ):
            return
        self.result.fetches += 1
        try:
            users = await self.reverse_fetcher(service, name)
        except Exception as exc:  # noqa: BLE001 - ters arama zinciri düşürmesin
            self.result.notes.append(f"Keybase ters arama ({node.label}): {type(exc).__name__}")
            return
        for username in users[:3]:
            child, created = self._node(
                f"profile:keybase:{username.lower()}",
                kind="profile",
                value=username.lower(),
                network="keybase",
                url=f"https://keybase.io/{username}",
            )
            if child is node:
                continue
            if created:
                child.depth = node.depth + 1
                child.parent_key = node.key
            self._add_evidence(
                child,
                Evidence(
                    relation="proof",
                    weight=node.trust * EDGE_WEIGHTS["proof"],
                    source_key=f"keybase-reverse:{node.key}",
                    source_label=node.label,
                    source_url=f"https://keybase.io/{username}",
                    note=f"Keybase {service} kanıtı {name}",
                ),
            )

    async def _expand_email(self, node: ChainNode) -> None:
        if not self._can_fetch() or self.email_fetcher is None:
            return
        self.result.fetches += 1
        try:
            intel = await self.email_fetcher(node.value)
        except Exception as exc:  # noqa: BLE001
            self.result.notes.append(f"{node.label}: {type(exc).__name__}")
            return
        if intel is None:
            return
        profile, _ = self._node(
            f"profile:gravatar:{intel.handle.lower()}",
            kind="profile",
            value=intel.handle.lower(),
            network="gravatar",
            url=intel.profile_url,
        )
        profile.depth = node.depth + 1
        profile.parent_key = profile.parent_key or node.key
        profile.expanded = True
        self._add_evidence(
            profile,
            Evidence(
                relation="proof",
                weight=node.trust * EDGE_WEIGHTS["proof"],
                source_key=node.key,
                source_label=node.label,
                source_url=intel.profile_url,
                note="e-posta özetinin Gravatar profili",
            ),
        )
        self._apply_intel(profile, intel)

    def _next(self) -> ChainNode | None:
        candidates = [
            node
            for node in self.nodes.values()
            if not node.expanded
            and node.depth <= self.max_depth
            and (node.anchor or node.trust >= EXPAND_MIN_TRUST or node._unverified())
            and node.trust > 0
        ]
        if not candidates:
            return None
        # Önce güvenilir ve sığ düğümler; tahmin düğümleri en sona.
        candidates.sort(
            key=lambda n: (n._unverified() and not n.anchor, n.depth, -n.trust, n.key)
        )
        return candidates[0]

    async def run(self) -> ChainResult:
        # Onaylı profillerin kullanıcı adı diğer API'lerde de denenir (tahmin;
        # ancak geri bağlantı/isim eşleşmesiyle doğrulanırsa zincire girer).
        for anchor in [n for n in self.nodes.values() if n.anchor and n.kind == "profile"]:
            self.add_anchor_handle(anchor.value)
        while self._can_fetch():
            node = self._next()
            if node is None:
                break
            await self._expand(node)
        return self.finish()

    def finish(self) -> ChainResult:
        """Sonucu kapatır; zaman aşımıyla kesilen çalışmada da çağrılabilir."""
        # Bütçe bitince okunmamış kullanıcı adı tahminleri doğrulanmamış kalır.
        for node in self.nodes.values():
            if not node.anchor and node._probe_only() and not node.expanded:
                node.trust = 0.0
        self.result.nodes = list(self.nodes.values())
        return self.result


# ---------------------------------------------------------------- DB


def anchors_from_items(engine: ChainEngine, items: Sequence[PoolItem]) -> int:
    """Havuz kayıtlarını çapa olarak ekler; eklenen çapa sayısını döndürür."""
    count = 0
    for item in items:
        attrs = item.attributes if isinstance(item.attributes, dict) else {}
        value = str(item.value or "").strip()
        trust = 1.0 if item.status == "confirmed" else max(0.3, float(item.confidence or 0.0))
        if item.kind in {"url", "person"} and value.startswith(("http://", "https://")):
            if engine.add_anchor_url(value, item_id=item.id, trust=trust) is not None:
                count += 1
        elif item.kind in {"username", "identifier"}:
            profile_url = str(attrs.get("profile_url") or "")
            if profile_url and engine.add_anchor_url(
                profile_url, item_id=item.id, trust=trust
            ) is not None:
                count += 1
            else:
                platform = str(attrs.get("platform") or "").lower()
                if platform in HANDLE_FETCHERS:
                    engine.add_anchor_profile(platform, value, item_id=item.id, trust=trust)
                    count += 1
                else:
                    engine.add_anchor_handle(value)
                    count += 1
        elif item.kind == "email":
            if engine.add_anchor_email(value, item_id=item.id, trust=trust) is not None:
                count += 1
        elif item.kind == "domain":
            if engine.add_anchor_url(
                f"https://{value.strip('.').lower()}", item_id=item.id, trust=trust
            ):
                count += 1
        elif item.kind == "name" and item.status == "confirmed":
            engine.add_name(value)
    return count


async def _default_intel_fetcher(token: str | None) -> IntelFetcher:
    client = HttpClient(timeout=12.0)

    async def fetch(network: str, handle: str) -> AccountIntel | None:
        fetcher = HANDLE_FETCHERS.get(network)
        if fetcher is None:
            return None
        if network == "github":
            return await fetcher(client, handle, token=token)  # type: ignore[call-arg]
        return await fetcher(client, handle)

    return fetch


def _default_page_fetcher() -> PageFetcher:
    async def fetch(url: str, trusted: bool) -> PageData | None:
        from showis.services.research import ToolError, _fetch_public_page

        try:
            page = await _fetch_public_page(url, respect_robots=not trusted)
        except ToolError:
            return None
        return PageData(
            url=page.url,
            title=page.title,
            links=list(page.links),
            rel_me=list(page.rel_me),
            emails=list(page.emails),
        )

    return fetch


async def _default_email_fetcher(email: str) -> AccountIntel | None:
    return await fetch_gravatar(HttpClient(timeout=12.0), email)


async def _default_reverse_fetcher(service: str, name: str) -> list[str]:
    return await keybase_reverse(HttpClient(timeout=12.0), service, name)


async def build_engine(
    db: AsyncSession,
    *,
    max_fetches: int = DEFAULT_MAX_FETCHES,
    max_depth: int = DEFAULT_MAX_DEPTH,
    intel_fetcher: IntelFetcher | None = None,
    page_fetcher: PageFetcher | None = None,
    email_fetcher: Callable[[str], Awaitable[AccountIntel | None]] | None = None,
    probe_handles: bool = True,
) -> ChainEngine:
    if intel_fetcher is None:
        from showis.services.connectors_factory import github_token

        intel_fetcher = await _default_intel_fetcher(await github_token(db) or None)
    engine = ChainEngine(
        intel_fetcher=intel_fetcher,
        page_fetcher=page_fetcher or _default_page_fetcher(),
        email_fetcher=email_fetcher or _default_email_fetcher,
        reverse_fetcher=_default_reverse_fetcher,
        max_fetches=max_fetches,
        max_depth=max_depth,
        probe_handles=probe_handles,
    )
    return engine


async def case_anchor_items(db: AsyncSession, case_id: str) -> list[PoolItem]:
    """Analistin onayladığı havuz kayıtları (çapa adayları)."""
    return list(
        (
            await db.execute(
                select(PoolItem)
                .where(PoolItem.case_id == case_id, PoolItem.status == "confirmed")
                .order_by(PoolItem.created_at.asc())
                .limit(200)
            )
        )
        .scalars()
        .all()
    )


async def add_case_anchors(engine: ChainEngine, db: AsyncSession, case: Case) -> int:
    """Hedef, seed ve onaylı havuz kayıtlarını çapa yapar."""
    count = 0
    target = (
        await db.execute(select(Target).where(Target.case_id == case.id))
    ).scalar_one_or_none()
    if target is not None:
        if target.first_name and target.last_name:
            engine.add_name(f"{target.first_name} {target.last_name}")
        if target.email and engine.add_anchor_email(target.email):
            count += 1
        if target.domain and engine.add_anchor_url(f"https://{target.domain}"):
            count += 1
        if target.nickname:
            engine.add_anchor_handle(target.nickname)
            count += 1
    seeds = (await db.execute(select(Seed).where(Seed.case_id == case.id))).scalars().all()
    for seed in seeds:
        if seed.url and engine.add_anchor_url(seed.url):
            count += 1
        if seed.email and engine.add_anchor_email(seed.email):
            count += 1
        if seed.username:
            platform = str(getattr(seed, "platform", "") or "").lower()
            if platform in HANDLE_FETCHERS:
                engine.add_anchor_profile(platform, seed.username)
            else:
                engine.add_anchor_handle(seed.username)
            count += 1
    count += anchors_from_items(engine, await case_anchor_items(db, case.id))
    return count


_NETWORK_LABEL: dict[str, str] = {
    "x": "X",
    "github": "GitHub",
    "gitlab": "GitLab",
    "linkedin": "LinkedIn",
    "instagram": "Instagram",
    "keybase": "Keybase",
    "devto": "dev.to",
    "bluesky": "Bluesky",
    "hackernews": "Hacker News",
    "gravatar": "Gravatar",
}


async def persist_chain(
    db: AsyncSession,
    *,
    case: Case,
    result: ChainResult,
    status: str = "proposed",
    actor: str = "chain",
    session_id: str | None = None,
) -> ChainResult:
    """Keşfedilen düğümleri havuza, kanıt kenarlarını ilişki grafına yazar."""
    if status not in {"proposed", "candidate"}:
        raise ValueError("zincir çıktısı yalnız proposed/candidate yazılabilir")
    by_key = {node.key: node for node in result.nodes}
    item_for: dict[str, PoolItem] = {}

    # Çapaların havuz kaydı varsa ilişki ucu olarak kullan.
    anchor_ids = [n.anchor_item_id for n in result.nodes if n.anchor and n.anchor_item_id]
    if anchor_ids:
        rows = (
            await db.execute(select(PoolItem).where(PoolItem.id.in_(anchor_ids)))
        ).scalars()
        by_id = {row.id: row for row in rows}
        for node in result.nodes:
            if node.anchor and node.anchor_item_id in by_id:
                item_for[node.key] = by_id[node.anchor_item_id]

    def chain_path(node: ChainNode) -> list[str]:
        path: list[str] = []
        cursor: str | None = node.parent_key
        guard = 0
        while cursor and cursor in by_key and guard < 8:
            path.append(by_key[cursor].label)
            cursor = by_key[cursor].parent_key
            guard += 1
        return list(reversed(path))

    for node in sorted(result.discovered(), key=lambda n: (n.depth, -n.trust)):
        provenance = {
            "method": "chain",
            "chain": chain_path(node),
            "evidence": [e.to_dict() for e in node.evidence[:10]],
            "independent_sources": node.independent_sources(),
        }
        if session_id:
            provenance["session_id"] = session_id
        if node.kind == "profile":
            network_label = _NETWORK_LABEL.get(node.network, node.network)
            kind, value = "url", node.url or ""
            if not value.startswith(("http://", "https://")):
                kind, value = "username", node.value
            label = f"{network_label} — {node.value}"
            if node.display_name:
                label = f"{label} ({node.display_name})"
            attributes: dict[str, Any] = {
                "platform": node.network,
                "username": node.value,
                "profile_url": node.url,
                "chain_trust": node.trust,
            }
            if node.avatar_url:
                attributes["avatar_url"] = node.avatar_url
            if node.display_name:
                attributes["display_name"] = node.display_name
        elif node.kind == "email":
            kind, value, label = "email", node.value, f"E-posta ({node.value})"
            attributes = {"chain_trust": node.trust}
        else:
            kind, value = "url", node.url or node.value
            label = f"Kişisel sayfa — {host_of(node.url)}"
            attributes = {"chain_trust": node.trust, "personal_site": True}
        try:
            item, created = await create_item(
                db,
                case_id=case.id,
                kind=kind,
                label=label,
                value=value,
                source="ai",
                status=status,
                confidence=node.trust,
                attributes=attributes,
                provenance=provenance,
                created_by=actor,
            )
        except (PoolValidationError, ValueError):
            continue
        if not created and item.source != "user" and item.status in {"proposed", "candidate"}:
            # Zincir kanıtı mevcut AI önerisinin güvenini yükseltebilir.
            if float(item.confidence or 0.0) < node.trust:
                item.confidence = node.trust
                result.updated_item_ids.append(item.id)
        if created:
            result.created_item_ids.append(item.id)
        item_for[node.key] = item
        if node.kind == "profile" and node.trust >= CONTEXT_MIN_TRUST:
            await _persist_context(db, case, item, node, status, actor, provenance, result)
        if node.kind == "profile":
            handle_item, handle_created = await _ensure_handle(
                db, case=case, node=node, status=status, actor=actor, provenance=provenance
            )
            if handle_item is not None:
                if handle_created:
                    result.created_item_ids.append(handle_item.id)
                await _relate(db, case, item, handle_item, "owns_account", node, result, actor)

    # Araştırmanın bulduğu ama platform API'sinde var olmayan profil: güveni düşür.
    for node in result.nodes:
        if (
            node.kind == "profile"
            and not node.anchor
            and node.expanded
            and node.trust == 0.0
            and node.url
        ):
            stale = (
                await db.execute(
                    select(PoolItem).where(
                        PoolItem.case_id == case.id,
                        PoolItem.kind == "url",
                        PoolItem.value == node.url,
                        PoolItem.source != "user",
                        PoolItem.status.in_(("proposed", "candidate")),
                    )
                )
            ).scalar_one_or_none()
            if stale is not None:
                stale.confidence = min(float(stale.confidence or 0.0), 0.1)
                stale.attributes = {**(stale.attributes or {}), "chain_missing": True}
                result.updated_item_ids.append(stale.id)

    for node in result.discovered():
        dst = item_for.get(node.key)
        if dst is None:
            continue
        for evidence in node.evidence:
            src = item_for.get(evidence.source_key)
            if src is None or src.id == dst.id:
                continue
            rel_type = "same_person" if (
                evidence.relation == "backlink"
                and node.kind == "profile"
                and node.trust >= SAME_PERSON_MIN_TRUST
            ) else ("uses_email" if node.kind == "email" else "linked_from")
            await _relate(db, case, src, dst, rel_type, node, result, actor, evidence=evidence)
    await db.flush()
    return result


async def _persist_context(
    db: AsyncSession,
    case: Case,
    account: PoolItem,
    node: ChainNode,
    status: str,
    actor: str,
    provenance: dict[str, Any],
    result: ChainResult,
) -> None:
    """Yüksek güvenli hesabın sahip yazdığı kurum/konum alanları (bağlam kaydı)."""
    for kind, raw, rel_type in (
        ("organization", node.company, "works_at"),
        ("location", node.location, "located_in"),
    ):
        value = " ".join(str(raw or "").split()).lstrip("@").strip()
        if len(value) < 2 or len(value) > 120:
            continue
        try:
            item, created = await create_item(
                db,
                case_id=case.id,
                kind=kind,
                label=value,
                value=value,
                source="ai",
                status=status,
                confidence=round(node.trust * 0.8, 4),
                attributes={"from_account": node.label},
                provenance=provenance,
                created_by=actor,
            )
        except (PoolValidationError, ValueError):
            continue
        if created:
            result.created_item_ids.append(item.id)
        await _relate(db, case, account, item, rel_type, node, result, actor)


async def _ensure_handle(
    db: AsyncSession,
    *,
    case: Case,
    node: ChainNode,
    status: str,
    actor: str,
    provenance: dict[str, Any],
) -> tuple[PoolItem | None, bool]:
    if not node.value or node.network in {"web"}:
        return None, False
    try:
        return await create_item(
            db,
            case_id=case.id,
            kind="username",
            label=f"{_NETWORK_LABEL.get(node.network, node.network)} — {node.value}",
            value=node.value,
            source="ai",
            status=status,
            confidence=node.trust,
            attributes={
                "platform": node.network,
                "profile_url": node.url,
                "chain_trust": node.trust,
            },
            provenance=provenance,
            created_by=actor,
        )
    except (PoolValidationError, ValueError):
        return None, False


async def _relate(
    db: AsyncSession,
    case: Case,
    src: PoolItem,
    dst: PoolItem,
    rel_type: str,
    node: ChainNode,
    result: ChainResult,
    actor: str,
    *,
    evidence: Evidence | None = None,
) -> None:
    entries = [
        {
            "rule": f"chain:{evidence.relation}" if evidence else "chain:account",
            "source": (evidence.source_url if evidence else node.url) or node.key,
            "weight": round((evidence.weight if evidence else node.trust) * 4, 3),
            "note": (evidence.note if evidence else "") or f"zincir güveni {node.trust:.2f}",
        }
    ]
    status = "candidate" if node.independent_sources() >= 2 else "proposed"
    try:
        _, created = await upsert_relation(
            db,
            case_id=case.id,
            src_item_id=src.id,
            dst_item_id=dst.id,
            type=rel_type,
            confidence=min(0.99, evidence.weight if evidence else node.trust),
            evidence=entries,
            status=status,
            source="pivot",
            created_by=actor,
        )
    except RelationError:
        return
    if created:
        result.relations += 1


async def run_case_chain(
    db: AsyncSession,
    *,
    case: Case,
    items: Sequence[PoolItem] | None = None,
    status: str = "proposed",
    actor: str = "chain",
    max_fetches: int = DEFAULT_MAX_FETCHES,
    max_depth: int = DEFAULT_MAX_DEPTH,
    session_id: str | None = None,
    engine: ChainEngine | None = None,
) -> ChainResult:
    """Vakanın çapalarından (veya verilen kayıtlardan) zinciri kurup yazar."""
    engine = engine or await build_engine(db, max_fetches=max_fetches, max_depth=max_depth)
    if items is None:
        await add_case_anchors(engine, db, case)
    else:
        # Kullanıcının açıkça seçtiği kayıtlar çapa olur; isimler tutarlılık için.
        target = (
            await db.execute(select(Target).where(Target.case_id == case.id))
        ).scalar_one_or_none()
        if target is not None and target.first_name and target.last_name:
            engine.add_name(f"{target.first_name} {target.last_name}")
        names = (
            await db.execute(
                select(PoolItem.value).where(
                    PoolItem.case_id == case.id,
                    PoolItem.kind == "name",
                    PoolItem.status == "confirmed",
                )
            )
        ).scalars()
        for name in names:
            engine.add_name(name)
        anchors_from_items(engine, items)
    result = await engine.run()
    await persist_chain(
        db, case=case, result=result, status=status, actor=actor, session_id=session_id
    )
    return result
