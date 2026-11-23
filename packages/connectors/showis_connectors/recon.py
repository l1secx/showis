"""Domain/IP keşif connector'ı — anahtarsız kamuya açık API'ler.

Kullanılan ücretsiz uçlar:

* RDAP (``rdap.org``): domain/IP kayıt sahibi, tarih, ad sunucuları, ASN/ülke.
* Google Public DNS (``dns.google``): A/AAAA/MX/NS/PTR çözümlemesi.
* crt.sh: Certificate Transparency alt alan adları (domain).
* Wayback CDX: arşivlenmiş URL sayısı/örnekleri (domain).

Hiçbiri giriş gerektirmez; sonuçlar tek entity + kanıt zinciri olarak döner.
"""

from __future__ import annotations

import ipaddress
import json
import uuid
from typing import Any
from urllib.parse import quote

from showis_contracts import (
    Edge,
    Entity,
    EntityType,
    Observation,
    ObservationKind,
    Platform,
    RiskTier,
    ScanContext,
    TaskSpec,
    make_entity_id,
)
from showis_engine.http import EvidenceStore, HttpClient

RDAP_DOMAIN = "https://rdap.org/domain/{value}"
RDAP_IP = "https://rdap.org/ip/{value}"
DNS_GOOGLE = "https://dns.google/resolve?name={name}&type={rtype}"
CRT_SH = "https://crt.sh/?q=%25.{domain}&output=json"
WAYBACK_CDX = (
    "https://web.archive.org/cdx/search/cdx"
    "?url={domain}&output=json&limit=10&filter=statuscode:200&collapse=urlkey"
)


class _BaseReconConnector:
    name: str = "recon"
    platform: Platform = Platform.WEB
    tier: RiskTier = RiskTier.A
    expected_kind: str = ""
    supported_kinds: set[str] = {"recon"}

    def __init__(
        self,
        http_client: HttpClient | None = None,
        evidence_store: EvidenceStore | None = None,
    ):
        self.http_client = http_client or HttpClient(enable_ssrf_guard=True)
        self.evidence_store = evidence_store or EvidenceStore()

    async def _get_json(self, url: str) -> tuple[Any, bytes, int] | None:
        try:
            resp = await self.http_client.get(
                url,
                headers={"Accept": "application/json"},
                check_robots=False,
                follow_redirects=True,
            )
        except Exception:
            return None
        if resp.status_code >= 400:
            return None
        raw = resp.content or b""
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return payload, raw, resp.status_code

    async def _collect(
        self, identifier: str
    ) -> tuple[dict[str, Any], list[Any]]:
        raise NotImplementedError

    def _entity(
        self,
        identifier: str,
        data: dict[str, Any],
        evidence_ids: list[str],
    ) -> list[Entity]:
        raise NotImplementedError

    def _task(self, identifier: str) -> TaskSpec:
        return TaskSpec(
            id=str(uuid.uuid4()),
            connector=self.name,
            platform=self.platform,
            kind="recon",
            params={"identifier": identifier},
            priority=2,
        )

    async def plan(self, ctx: ScanContext) -> list[TaskSpec]:
        if ctx.target.kind != self.expected_kind:
            return []
        ident = ctx.target.identifier
        if not ident:
            return []
        return [self._task(ident)]

    async def run(self, task: TaskSpec, ctx: ScanContext) -> list[Observation]:
        ident = str(task.params.get("identifier") or "").strip()
        if not ident:
            return []
        data, evidence = await self._collect(ident)
        if not data and not evidence:
            return [
                Observation(
                    id=str(uuid.uuid4()),
                    connector=self.name,
                    platform=self.platform,
                    kind=ObservationKind.PROFILE,
                    subject=ident,
                    tier=self.tier,
                    data={"identifier": ident, "exists": False},
                )
            ]
        return [
            Observation(
                id=str(uuid.uuid4()),
                connector=self.name,
                platform=self.platform,
                kind=ObservationKind.PROFILE,
                subject=ident,
                url=data.get("url"),
                tier=self.tier,
                data={"identifier": ident, "exists": True, **data},
                evidence=evidence,
            )
        ]

    def normalize(self, obs: Observation) -> tuple[list[Entity], list[Edge]]:
        if obs.error or obs.blocked or not obs.data or not obs.data.get("exists"):
            return [], []
        ident = str(obs.data.get("identifier") or obs.subject)
        ev_ids = [ev.id for ev in obs.evidence]
        return self._entity(ident, obs.data, ev_ids), []


class DomainReconConnector(_BaseReconConnector):
    name = "domain_recon"
    expected_kind = "domain"

    async def _collect(self, identifier: str) -> tuple[dict[str, Any], list[Any]]:
        domain = identifier.lower().strip().rstrip(".")
        data: dict[str, Any] = {
            "url": f"https://{domain}",
            "domain": domain,
            "confidence_hint": 0.8,
        }
        evidence: list[Any] = []

        rdap = await self._get_json(RDAP_DOMAIN.format(value=quote(domain, safe="")))
        if rdap:
            payload, raw, status = rdap
            evidence.append(
                self.evidence_store.store_bytes(
                    raw,
                    source_url=RDAP_DOMAIN.format(value=quote(domain, safe="")),
                    kind="json",
                    http_status=status,
                )
            )
            data.update(self._parse_rdap(payload))

        dns_values: dict[str, list[str]] = {}
        for rtype in ("A", "AAAA", "MX", "NS"):
            found = await self._get_json(
                DNS_GOOGLE.format(name=quote(domain, safe=""), rtype=rtype)
            )
            if not found:
                continue
            payload, raw, status = found
            evidence.append(
                self.evidence_store.store_bytes(
                    raw,
                    source_url=DNS_GOOGLE.format(
                        name=quote(domain, safe=""), rtype=rtype
                    ),
                    kind="json",
                    http_status=status,
                )
            )
            values = []
            for answer in payload.get("Answer") or []:
                value = str(answer.get("data") or "").strip().rstrip(".")
                if value:
                    values.append(value)
            if values:
                dns_values[rtype.lower()] = values[:20]
        if dns_values:
            data["dns"] = dns_values
            if "a" in dns_values:
                data["ip"] = dns_values["a"][0]

        crt = await self._get_json(CRT_SH.format(domain=quote(domain, safe="")))
        if crt:
            payload, raw, status = crt
            evidence.append(
                self.evidence_store.store_bytes(
                    raw,
                    source_url=CRT_SH.format(domain=quote(domain, safe="")),
                    kind="json",
                    http_status=status,
                )
            )
            names = set()
            for row in payload if isinstance(payload, list) else []:
                for name in str(row.get("name_value") or "").splitlines():
                    name = name.strip().lower().lstrip("*.")
                    if name and name.endswith(domain):
                        names.add(name)
            if names:
                data["subdomains"] = sorted(names)[:100]

        wayback = await self._get_json(
            WAYBACK_CDX.format(domain=quote(domain, safe=""))
        )
        if wayback:
            payload, raw, status = wayback
            evidence.append(
                self.evidence_store.store_bytes(
                    raw,
                    source_url=WAYBACK_CDX.format(domain=quote(domain, safe="")),
                    kind="json",
                    http_status=status,
                )
            )
            rows = payload if isinstance(payload, list) else []
            if len(rows) > 1:
                data["archive_samples"] = [
                    {"timestamp": row[0], "url": row[1]}
                    for row in rows[1:6]
                    if isinstance(row, list) and len(row) >= 2
                ]
        return data, evidence

    @staticmethod
    def _parse_rdap(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        out: dict[str, Any] = {}
        for event in payload.get("events") or []:
            action = str(event.get("eventAction") or "").lower()
            date = event.get("eventDate")
            if action == "registration" and date:
                out["created_at"] = date
            elif action == "expiration" and date:
                out["expires_at"] = date
        nameservers = []
        for ns in payload.get("nameservers") or []:
            name = str(ns.get("ldhName") or "").lower()
            if name:
                nameservers.append(name)
        if nameservers:
            out["nameservers"] = nameservers
        entities = payload.get("entities") or []
        for entity in entities:
            vcard = (entity.get("vcardArray") or [None, []])[1]
            for item in vcard:
                if item and item[0] == "fn":
                    out.setdefault("registrar", str(item[3]))
        return out

    def _entity(
        self, identifier: str, data: dict[str, Any], evidence_ids: list[str]
    ) -> list[Entity]:
        return [
            Entity(
                id=make_entity_id(self.platform.value, f"domain:{identifier}"),
                type=EntityType.DOMAIN,
                platform=self.platform,
                label=identifier,
                url=data.get("url") or f"https://{identifier}",
                attributes={
                    "domain": identifier,
                    "confidence_hint": data.get("confidence_hint", 0.8),
                    **{k: v for k, v in data.items() if k != "identifier"},
                },
                evidence_ids=evidence_ids,
            )
        ]


class IpReconConnector(_BaseReconConnector):
    name = "ip_recon"
    expected_kind = "ip"

    async def _collect(self, identifier: str) -> tuple[dict[str, Any], list[Any]]:
        address = str(identifier).strip()
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return {}, []
        data: dict[str, Any] = {
            "url": f"https://ipinfo.io/{ip}",
            "ip": str(ip),
            "version": ip.version,
            "confidence_hint": 0.8,
        }
        evidence: list[Any] = []

        rdap = await self._get_json(RDAP_IP.format(value=quote(str(ip), safe="")))
        if rdap:
            payload, raw, status = rdap
            evidence.append(
                self.evidence_store.store_bytes(
                    raw,
                    source_url=RDAP_IP.format(value=quote(str(ip), safe="")),
                    kind="json",
                    http_status=status,
                )
            )
            if isinstance(payload, dict):
                data["name"] = payload.get("name")
                data["country"] = payload.get("country")
                data["handle"] = payload.get("handle")
                data["start_address"] = payload.get("startAddress")
                data["end_address"] = payload.get("endAddress")
                entities = payload.get("entities") or []
                for entity in entities:
                    vcard = (entity.get("vcardArray") or [None, []])[1]
                    for item in vcard:
                        if item and item[0] == "fn":
                            data.setdefault("org", str(item[3]))

        ptr_name = self._reverse_name(ip)
        if ptr_name:
            dns = await self._get_json(
                DNS_GOOGLE.format(name=quote(ptr_name, safe=""), rtype="PTR")
            )
            if dns:
                payload, raw, status = dns
                evidence.append(
                    self.evidence_store.store_bytes(
                        raw,
                        source_url=DNS_GOOGLE.format(
                            name=quote(ptr_name, safe=""), rtype="PTR"
                        ),
                        kind="json",
                        http_status=status,
                    )
                )
                values = [
                    str(item.get("data") or "").strip().rstrip(".")
                    for item in payload.get("Answer") or []
                ]
                if values:
                    data["ptr"] = values[0]
        return data, evidence

    @staticmethod
    def _reverse_name(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
        if ip.version == 4:
            return ".".join(reversed(str(ip).split("."))) + ".in-addr.arpa"
        nibbles = ip.exploded.replace(":", "")
        return ".".join(reversed(nibbles)) + ".ip6.arpa"

    def _entity(
        self, identifier: str, data: dict[str, Any], evidence_ids: list[str]
    ) -> list[Entity]:
        return [
            Entity(
                id=make_entity_id(self.platform.value, f"ip:{identifier}"),
                type=EntityType.IP,
                platform=self.platform,
                label=identifier,
                url=data.get("url"),
                attributes={
                    "ip": identifier,
                    "confidence_hint": data.get("confidence_hint", 0.8),
                    **{k: v for k, v in data.items() if k != "identifier"},
                },
                evidence_ids=evidence_ids,
            )
        ]
