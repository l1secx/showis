"""Guvenlik: yasak host'lara istek atilmadiginin dogrulanmasi (SEC3, S2-3).

1. Runtime: guard_url / assert_host_not_forbidden yasak host'lari reddeder.
2. Statik: kaynak kodda (packages/, apps/) yasak host literal'i bulunmaz
   (policy.py'deki tanim listesi haric).
"""

from pathlib import Path

import pytest
from showis_engine.policy import (
    PolicyViolationError,
    assert_host_not_forbidden,
    guard_url,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = [REPO_ROOT / "packages", REPO_ROOT / "apps"]

# Yasak host literal'leri — politika taniminin bulundugu dosya haric tutulur
FORBIDDEN_LITERALS = ["labs.hackthebox.com", "hubspot.com"]
ALLOWLIST_FILES = {
    REPO_ROOT / "packages/engine/showis_engine/policy.py",
}


@pytest.mark.parametrize(
    "url",
    [
        "https://labs.hackthebox.com/users/profile",
        "https://labs.hackthebox.com/",
        "http://labs.hackthebox.com:443/api",
        "https://evil.hubspot.com/page",
        "https://www.hackthebox.com/api/v1/users",
        "https://pastebin.com/raw/abc",
    ],
)
def test_forbidden_hosts_runtime_guard(url: str):
    with pytest.raises(PolicyViolationError):
        guard_url(url)


@pytest.mark.parametrize(
    "host",
    ["labs.hackthebox.com", "sub.labs.hackthebox.com", "tracker.hubspot.com"],
)
def test_assert_host_not_forbidden(host: str):
    with pytest.raises(PolicyViolationError):
        assert_host_not_forbidden(host)


def test_allowed_hosts_pass():
    # guard_url DNS cozumlemesi yapar; public host'lar guard'dan gecer
    # (cozumleme basarisiz olsa bile gaierror yutulur)
    guard_url("https://app.hackthebox.com/profile/abc123")
    guard_url("https://tryhackme.com/p/foo")
    guard_url("https://api.github.com/users/foo")


def test_no_forbidden_host_literals_in_source():
    """Kaynak kodda yasak host literal'i taramasi (policy.py tanim haric)."""
    offenders: list[str] = []
    for scan_dir in SCAN_DIRS:
        for py_file in scan_dir.rglob("*.py"):
            if "__pycache__" in py_file.parts:
                continue
            if py_file in ALLOWLIST_FILES:
                continue
            try:
                content = py_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for literal in FORBIDDEN_LITERALS:
                if literal in content:
                    offenders.append(f"{py_file.relative_to(REPO_ROOT)}: {literal}")
    assert offenders == [], f"Yasak host literal'i kaynak kodda bulundu: {offenders}"
