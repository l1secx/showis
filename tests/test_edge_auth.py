"""Edge forward_auth hız sınırı."""

from showis.api.v1.auth import _EDGE_FAILURES, _edge_limited, _record_failure


def test_edge_rate_limit_penceresi():
    _EDGE_FAILURES.clear()
    try:
        for _ in range(10):
            assert _edge_limited("edge:test") is False
            _record_failure("edge:test")
        assert _edge_limited("edge:test") is True
        assert _edge_limited("edge:other") is False
        assert _edge_limited("edge:test", 11) is False
    finally:
        _EDGE_FAILURES.clear()
