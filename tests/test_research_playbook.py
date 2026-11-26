"""Derin araştırma oyun kitabı ve bağlantı toplayıcı connector testleri."""

from __future__ import annotations

from showis.services.research_playbook import (
    build_baseline_queries,
    build_followup_queries,
    build_identity_profile,
    canonical_profile,
    fallback_bio,
    is_aggregator_url,
    parse_page_intel,
)
from showis_connectors.linkfollow import LinkFollowConnector
from showis_contracts import Observation, ObservationKind, Platform, RiskTier


def test_identity_profile_name_and_seed_handle_merge():
    profile = build_identity_profile(
        first_name="Kerem",
        last_name="Boz",
        nickname="keremboz",
        seed_usernames=["keremboz", "kboz"],
        pool_items=[
            {"kind": "email", "value": "kerem@example.com", "attributes": {}},
            {"kind": "organization", "value": "Acme", "attributes": {}},
            {
                "kind": "url",
                "value": "https://linktr.ee/kboz",
                "attributes": {},
            },
        ],
    )
    assert profile.full_name == "Kerem Boz"
    assert profile.handles[:2] == ("keremboz", "kboz")
    assert "kerem@example.com" in profile.emails
    assert "Acme" in profile.organizations


def test_baseline_queries_join_name_handle_and_aggregators():
    profile = build_identity_profile(
        first_name="Kerem", last_name="Boz", nickname="keremboz"
    )
    queries = {item.text for item in build_baseline_queries(profile)}
    assert '"Kerem Boz" "keremboz"' in queries
    assert '"keremboz" "Kerem Boz"' in queries
    assert '"keremboz" site:linktr.ee' in queries
    assert any("linktree" in query for query in queries)


def test_followup_queries_use_discovered_handles_and_dedupe():
    profile = build_identity_profile(
        first_name="Kerem", last_name="Boz", nickname="keremboz"
    )
    known = [item.text for item in build_baseline_queries(profile)]
    followups = build_followup_queries(
        profile, known_queries=known, discovered_handles=["kboz"]
    )
    texts = [item.text for item in followups]
    assert any("kboz" in text for text in texts)
    assert all(text not in known for text in texts)


def test_parse_linktree_page_extracts_social_profiles_not_brand_links():
    markup = """
    <html><head><title>Kerem Boz | Linktree</title>
    <meta name="description" content="Kişisel bağlantılar"></head>
    <body>
      <a href="https://instagram.com/kboz">Instagram</a>
      <a href="https://x.com/keremboz">X</a>
      <a href="https://github.com/keremboz">GitHub</a>
      <a href="https://linkedin.com/in/kerem-boz">LinkedIn</a>
      <a href="https://x.com/keremboz">X</a>
      <a href="https://linktr.ee/linktree">Linktree</a>
      <a href="https://instagram.com/linktree">Instagram brand</a>
      <a href="mailto:kerem@example.com">E-posta</a>
      <script>window.__DATA__={"url":"https://www.threads.com/@l1sec"}</script>
    </body></html>
    """
    intel = parse_page_intel("https://linktr.ee/kboz", markup)
    pairs = {(item.platform, item.identifier) for item in intel.profiles}
    assert (Platform.INSTAGRAM, "kboz") in pairs
    assert (Platform.GITHUB, "keremboz") in pairs
    assert (Platform.WEB, "kerem-boz") in pairs
    assert (Platform.WEB, "keremboz") in pairs
    assert (Platform.WEB, "l1sec") in pairs
    assert all(identifier != "linktree" for _, identifier in pairs)
    # Linktree sayfasının kendi öneri profilleri hedef bulgusu olmamalı.
    assert all(platform != Platform.LINKTREE for platform, _ in pairs)
    assert "kerem@example.com" in intel.emails
    assert intel.title == "Kerem Boz | Linktree"


def test_canonical_profile_handles_aggregator_and_rejects_brand():
    assert is_aggregator_url("https://linktr.ee/keremboz")
    profile = canonical_profile("https://linktr.ee/keremboz")
    assert profile is not None and profile.platform == Platform.LINKTREE
    assert canonical_profile("https://linktr.ee/linktree") is None
    # Depo/issue yolları profil değildir.
    assert canonical_profile("https://github.com/keybase/client/issues") is None
    assert canonical_profile("https://x.com/keremboz/status/123") is None
    assert canonical_profile("https://linktr.ee/solutions/manage") is None
    assert canonical_profile("https://ugc.production.linktr.ee/abc.png") is None


def test_fallback_bio_contains_known_identity_and_sources():
    profile = build_identity_profile(
        first_name="Kerem", last_name="Boz", nickname="keremboz"
    )
    bio = fallback_bio(
        profile,
        [
            {"kind": "username", "value": "keremboz"},
            {
                "kind": "url",
                "value": "https://github.com/keremboz",
                "provenance": {"url": "https://github.com/keremboz"},
            },
        ],
    )
    assert "# Kerem Boz" in bio
    assert "keremboz" in bio
    assert "https://github.com/keremboz" in bio


def test_linkfollow_normalizes_aggregator_links_to_entities_and_edges():
    connector = LinkFollowConnector()
    observation = Observation(
        id="obs-1",
        connector="linkfollow",
        platform=Platform.WEB,
        kind=ObservationKind.PROFILE,
        subject="https://linktr.ee/kboz",
        url="https://linktr.ee/kboz",
        tier=RiskTier.B,
        data={
            "url": "https://linktr.ee/kboz",
            "title": "Kerem Boz",
            "text": "Kişisel bağlantılar",
            "links": [
                {
                    "url": "https://instagram.com/kboz",
                    "platform": "instagram",
                    "username": "kboz",
                },
                {
                    "url": "https://github.com/keremboz",
                    "platform": "github",
                    "username": "keremboz",
                },
            ],
            "emails": ["kerem@example.com"],
        },
    )
    entities, edges = connector.normalize(observation)
    by_platform = {entity.platform: entity for entity in entities}
    assert Platform.WEB in by_platform
    assert Platform.INSTAGRAM in by_platform
    assert Platform.GITHUB in by_platform
    assert by_platform[Platform.WEB].attributes["social"]["instagram"] == "kboz"
    assert len(edges) == 2
    assert all(edge.type.value == "cross_link" for edge in edges)


def test_build_chain_queries_prioritises_verified_seeds_and_skips_known():
    from showis.services.research_playbook import build_chain_queries

    queries = build_chain_queries(
        full_name="Ali Yılmaz",
        handles=["ali_dev", "ali_dev", "x"],
        emails=["ali@ali.dev"],
        domains=["ali.dev"],
        known=['"ali.dev"'],
    )
    texts = [q.text for q in queries]
    assert texts == ['"ali_dev"', '"ali_dev" "Ali Yılmaz"', '"ali@ali.dev"']
    assert all(q.priority == 1 for q in queries)
