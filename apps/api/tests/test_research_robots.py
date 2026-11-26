"""DEC-001: AI/tarama kaynaklı havuz URL'leri robots'a tabidir.

Yalnız kullanıcının açıkça verdiği seed/havuz URL'si robots istisnasıdır.
"""

from __future__ import annotations

from showis.services.research import identity_url_flags
from showis.services.research_playbook import build_identity_profile


def test_seed_urls_trusted_ai_pool_urls_not():
    profile = build_identity_profile(
        first_name="Kerem",
        last_name="Boz",
        seed_urls=["https://seed.example/kerem"],
        pool_items=[
            {
                "kind": "url",
                "value": "https://ai.example/kerem",
                "source": "ai",
                "status": "proposed",
                "attributes": {},
            },
            {
                "kind": "url",
                "value": "https://scan.example/kerem",
                "source": "scan",
                "status": "candidate",
                "attributes": {},
            },
            {
                "kind": "url",
                "value": "https://user.example/kerem",
                "source": "user",
                "status": "confirmed",
                "attributes": {},
            },
            {
                "kind": "url",
                "value": "https://approved.example/kerem",
                "source": "ai",
                "status": "confirmed",
                "attributes": {},
            },
        ],
    )

    flags = dict(identity_url_flags(profile))
    assert flags["https://seed.example/kerem"] is True
    assert flags["https://ai.example/kerem"] is False
    assert flags["https://scan.example/kerem"] is False
    assert flags["https://user.example/kerem"] is True
    # Kullanıcı onayı (status=confirmed) AI kaynağını istisnaya taşır.
    assert flags["https://approved.example/kerem"] is True


def test_identity_url_flags_returns_at_most_six():
    profile = build_identity_profile(
        seed_urls=[f"https://seed.example/{index}" for index in range(10)]
    )
    assert len(identity_url_flags(profile)) == 6
    assert all(trusted for _, trusted in identity_url_flags(profile))
