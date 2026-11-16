"""T-347 — rendered/HTB/THM kimlik semantiği: uuid değil kullanıcı adı.

Kök sorun: `rendered.normalize` PROFILE gözleminde kimliği
`data["username"] or data["identifier"]` üzerinden kurar. HTB `parse_profile`
`username`'i siliyordu → entity id/label UUID oluyordu; sonuçta
`identity_edges.entity_username()` uuid'yi kullanıcı adı sanıyor, HTB ↔ GitHub
"ortak kullanıcı adı" kenarı hiç üretilemiyordu.

Bu testler kimlik sözleşmesini kilitler:

* profilde okunabilir kullanıcı adı varsa entity id `platform:<username>`,
* yoksa platform kimliği (uuid) kullanılır ama **label asla uuid olmaz**,
* uuid `attributes.platform_user_id` olarak korunur,
* HTB + GitHub aynı kullanıcı adı → SAME_PERSON kenarı,
* authenticity/edge için beklenen attribute'lar dolu.
"""

from __future__ import annotations

from showis_connectors.hackthebox import HackTheBoxConnector
from showis_connectors.tryhackme import TryHackMeConnector
from showis_contracts import Observation, ObservationKind, Platform, RiskTier
from showis_engine.identity_edges import build_identity_edges

HTB_UUID = "019c93e0-2df1-71d2-b072-9aed50d06661"

HTB_HTML_WITH_USERNAME = f"""
<html><head>
<title>kboz | Hack The Box</title>
<meta property="og:title" content="kboz | Hack The Box">
<meta property="og:image" content="https://htb.example/avatar/kboz.png">
<meta property="og:description" content="Security researcher from Ankara">
</head><body>
<script id="__NEXT_DATA__" type="application/json">
{{"props":{{"pageProps":{{"user":{{"id":"{HTB_UUID}","username":"kboz","points":420,
"joinedAt":"2021-03-04T10:00:00.000Z"}}}}}}}}
</script>
<div>Rank: Pro Hacker</div>
<div>Machines Completed 57</div>
<div>Country Turkey</div>
<div>Points 420</div>
</body></html>
"""

HTB_HTML_UUID_ONLY = f"""
<html><head><title>{HTB_UUID} | Hack The Box</title>
<meta property="og:image" content="https://htb.example/avatar/default.png"></head><body>
<div>Rank: Noob</div><div>Machines Completed 1</div><div>Country Turkey</div>
<div>{HTB_UUID}</div>
<div>Bu sayfa profil sahibi hakkinda yeterli metin icermiyor, sadece kimlik var.</div>
</body></html>
"""

THM_HTML = """
<html><head><title>keremboz | TryHackMe</title>
<meta property="og:title" content="keremboz | TryHackMe">
<meta property="og:image" content="https://thm.example/avatar/kb.png"></head><body>
<div>Rank 1234</div><div>Streak 9</div><div>Rooms Completed 42</div>
<div>github.com/keremboz</div>
<div>Profil aciklamasi: ogrenci ve guvenlik arastirmacisi.</div>
</body></html>
"""


def _htb() -> HackTheBoxConnector:
    return HackTheBoxConnector()


def _thm() -> TryHackMeConnector:
    return TryHackMeConnector()


def _profile_obs(
    connector_name: str, platform: Platform, data: dict
) -> Observation:
    return Observation(
        id="obs-1",
        connector=connector_name,
        platform=platform,
        kind=ObservationKind.PROFILE,
        subject=str(data.get("username") or data.get("identifier") or ""),
        url="https://example/profile",
        tier=RiskTier.B,
        data=data,
    )


# ---------------------------------------------------------------------------
# (a) username varsa kimlik ondan
# ---------------------------------------------------------------------------


def test_htb_profilinde_username_varsa_entity_id_username_olur():
    connector = _htb()
    htb_url = f"https://app.hackthebox.com/profile/{HTB_UUID}"
    data = connector.parse_profile(
        HTB_HTML_WITH_USERNAME, htb_url, HTB_UUID
    )
    assert data["username"] == "kboz"
    assert data["platform_user_id"] == HTB_UUID

    entities, _ = connector.normalize(
        _profile_obs("hackthebox", Platform.HACKTHEBOX, data)
    )
    assert len(entities) == 1
    entity = entities[0]
    assert entity.id == "hackthebox:kboz"
    assert entity.label == "kboz"
    assert entity.attributes["username"] == "kboz"
    assert entity.attributes["platform_user_id"] == HTB_UUID


def test_thm_kimlik_kullanici_adi():
    connector = _thm()
    data = connector.parse_profile(THM_HTML, "https://tryhackme.com/p/keremboz", "keremboz")
    assert data["username"] == "keremboz"

    entities, _ = connector.normalize(_profile_obs("tryhackme", Platform.TRYHACKME, data))
    assert entities[0].id == "tryhackme:keremboz"
    assert entities[0].label == "keremboz"


# ---------------------------------------------------------------------------
# (b) username yoksa platform kimliği (uuid) — daraltılmış/olduğu gibi
# ---------------------------------------------------------------------------


def test_username_yoksa_uuid_kimlik_olarak_kullanilir():
    connector = _htb()
    data = connector.parse_profile(
        HTB_HTML_UUID_ONLY, f"https://app.hackthebox.com/profile/{HTB_UUID}", HTB_UUID
    )
    assert "username" not in data or not data.get("username")
    assert data["platform_user_id"] == HTB_UUID

    entities, _ = connector.normalize(
        _profile_obs("hackthebox", Platform.HACKTHEBOX, data)
    )
    assert entities[0].id == f"hackthebox:{HTB_UUID}"
    assert entities[0].attributes["platform_user_id"] == HTB_UUID


# ---------------------------------------------------------------------------
# (c) label asla uuid değil
# ---------------------------------------------------------------------------


def test_label_asla_uuid_degil():
    connector = _htb()
    data = connector.parse_profile(
        HTB_HTML_UUID_ONLY, f"https://app.hackthebox.com/profile/{HTB_UUID}", HTB_UUID
    )
    entities, _ = connector.normalize(
        _profile_obs("hackthebox", Platform.HACKTHEBOX, data)
    )
    label = entities[0].label
    assert label != HTB_UUID
    assert not label.startswith(HTB_UUID[:8]), "uuid tabanlı etiket okunmaz olur"


def test_uuid_display_name_kullanici_adi_sayilmaz():
    """og:title uuid içeriyorsa o da kullanıcı adı olarak kabul edilmez."""
    connector = _htb()
    data = connector.parse_profile(
        HTB_HTML_UUID_ONLY, f"https://app.hackthebox.com/profile/{HTB_UUID}", HTB_UUID
    )
    assert not connector._extract_username(HTB_HTML_UUID_ONLY, data, HTB_UUID)


# ---------------------------------------------------------------------------
# (d) HTB + GitHub aynı username → SAME_PERSON
# ---------------------------------------------------------------------------


def test_htb_ve_github_ayni_username_same_person_kenari():
    from showis_contracts import Entity, EntityType

    connector = _htb()
    data = connector.parse_profile(
        HTB_HTML_WITH_USERNAME,
        f"https://app.hackthebox.com/profile/{HTB_UUID}",
        HTB_UUID,
    )
    htb_entities, _ = connector.normalize(
        _profile_obs("hackthebox", Platform.HACKTHEBOX, data)
    )
    github = Entity(
        id="github:kboz",
        type=EntityType.ACCOUNT,
        platform=Platform.GITHUB,
        label="kboz",
        attributes={"username": "kboz"},
    )

    edges = build_identity_edges([htb_entities[0], github])
    same_person = [e for e in edges if e.type.value == "same_person"]
    assert len(same_person) == 1
    assert {same_person[0].source_entity_id, same_person[0].target_entity_id} == {
        "hackthebox:kboz",
        "github:kboz",
    }
    assert "kboz" in same_person[0].reason


def test_uuid_kimlikli_htb_github_ile_baglanmaz():
    """Kullanıcı adı yoksa (uuid) yanlış pozitif kenar üretilmemeli."""
    from showis_contracts import Entity, EntityType

    connector = _htb()
    data = connector.parse_profile(
        HTB_HTML_UUID_ONLY, f"https://app.hackthebox.com/profile/{HTB_UUID}", HTB_UUID
    )
    htb_entities, _ = connector.normalize(
        _profile_obs("hackthebox", Platform.HACKTHEBOX, data)
    )
    github = Entity(
        id="github:kboz",
        type=EntityType.ACCOUNT,
        platform=Platform.GITHUB,
        label="kboz",
        attributes={"username": "kboz"},
    )
    assert build_identity_edges([htb_entities[0], github]) == []


# ---------------------------------------------------------------------------
# (e) attributes: authenticity + edge sinyalleri
# ---------------------------------------------------------------------------


def test_htb_attributes_authenticity_ve_edge_alanlari():
    connector = _htb()
    data = connector.parse_profile(
        HTB_HTML_WITH_USERNAME,
        f"https://app.hackthebox.com/profile/{HTB_UUID}",
        HTB_UUID,
    )
    attrs = connector._entity_attributes(data)

    assert attrs["username"] == "kboz"
    assert attrs["name"] == "kboz"
    assert attrs["platform_user_id"] == HTB_UUID
    assert attrs["avatar_url"].endswith("kboz.png")
    assert attrs["country"].startswith("Turkey")
    assert attrs["rank"] == "Pro"  # _RANK_RE tek kelime yakalar
    assert attrs["machines_completed"] == 57
    assert attrs["points"] == 420
    assert attrs["joined_at"].startswith("2021-03-04")
    assert attrs["created_at"] == attrs["joined_at"]  # authenticity hesabı için alias


def test_thm_attributes_kimlik_alanlari():
    connector = _thm()
    data = connector.parse_profile(THM_HTML, "https://tryhackme.com/p/keremboz", "keremboz")
    attrs = connector._entity_attributes(data)
    assert attrs["username"] == "keremboz"
    assert attrs["platform_user_id"] == "keremboz"
    assert attrs["name"] == "keremboz"
    assert attrs["avatar_url"].endswith("kb.png")
