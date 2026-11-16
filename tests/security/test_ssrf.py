import pytest
from showis_engine.policy import (
    PolicyViolationError,
    SSRFViolationError,
    assert_host_not_forbidden,
    guard_url,
)


def test_ssrf_blocks_private_and_loopback_ips():
    bad_urls = [
        "http://127.0.0.1/admin",
        "http://localhost:8080/metrics",
        "http://192.168.1.100/config",
        "http://10.0.0.5/secret",
        "http://172.16.0.1/status",
        "http://169.254.169.254/latest/meta-data/",
        "ftp://example.com/file",
        "file:///etc/passwd",
    ]

    for bad in bad_urls:
        with pytest.raises(SSRFViolationError):
            guard_url(bad)


def test_assert_host_not_forbidden():
    with pytest.raises(PolicyViolationError):
        assert_host_not_forbidden("labs.hackthebox.com")

    with pytest.raises(PolicyViolationError):
        assert_host_not_forbidden("http://labs.hackthebox.com/api")

    with pytest.raises(PolicyViolationError):
        assert_host_not_forbidden("https://track.hubspot.com")

    # Allowed host
    assert_host_not_forbidden("https://api.github.com")
