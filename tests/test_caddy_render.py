"""Caddy render — çok kullanıcılı basic auth ve TLS bloğu."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "infra" / "scripts" / "caddy-render-config.sh"
CADDYFILE = ROOT / "infra" / "caddy" / "Caddyfile"


def _render(tmp_path: Path, **env: str) -> str:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    caddy = fake_bin / "caddy"
    # `exec caddy run ...` satırı testte gerçek Caddy başlatmasın.
    caddy.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    caddy.chmod(0o755)

    output = tmp_path / "Caddyfile.rendered"
    base = {
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
        "CADDY_SOURCE": str(CADDYFILE),
        "CADDY_RENDERED": str(output),
    }
    base.update(env)
    subprocess.run(
        ["/bin/sh", str(SCRIPT)],
        env=base,
        check=True,
        capture_output=True,
        text=True,
    )
    return output.read_text(encoding="utf-8")


def test_caddyfile_tls_ve_aktor_basligi_var():
    text = CADDYFILE.read_text(encoding="utf-8")
    assert "__TLS_HOSTS_PLACEHOLDER__" in text
    assert "tls internal" in text
    assert "__ACTOR_HEADER_PLACEHOLDER__" in text
    assert text.count("__ACTOR_HEADER_PLACEHOLDER__") == 4


def test_tek_kullanici_geriye_uyumlu(tmp_path):
    rendered = _render(
        tmp_path,
        SHOWIS_CADDY_USER="analyst",
        SHOWIS_CADDY_PASSWORD_HASH="$2a$14$abcdefghijklmnopqrstuv",
    )
    assert "basicauth @gate {" in rendered
    assert "\t\tanalyst $2a$14$abcdefghijklmnopqrstuv" in rendered
    assert "__BASIC_AUTH_PLACEHOLDER__" not in rendered


def test_cok_kullanicili_liste(tmp_path):
    rendered = _render(
        tmp_path,
        SHOWIS_CADDY_USERS=(
            "analyst:$2a$14$aaaa;viewer:$2a$14$bbbb;broken"
        ),
    )
    assert "\t\tanalyst $2a$14$aaaa" in rendered
    assert "\t\tviewer $2a$14$bbbb" in rendered
    assert "broken" not in rendered
    assert "https://localhost:8443, https://127.0.0.1:8443 {" in rendered


def test_forward_auth_modu(tmp_path):
    rendered = _render(
        tmp_path,
        SHOWIS_CADDY_USERS="analyst:$2a$14$aaaa",
        SHOWIS_EDGE_FORWARD_AUTH="1",
    )
    assert "forward_auth @gate api:8000 {" in rendered
    assert "uri /api/v1/auth/edge" in rendered
    assert "copy_headers X-Actor" in rendered
    assert "header_up X-Actor {http.request.header.X-Actor}" in rendered
    assert "basicauth @gate {" not in rendered


def test_kimlik_yoksa_render_fail_closed(tmp_path):
    """DEC-008: edge auth zorunlu; kimlik yoksa render HATA verir (auth'suz mod yok)."""
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        _render(tmp_path)
    assert excinfo.value.returncode == 1
    stderr = excinfo.value.stderr or ""
    assert "edge kimlik dogrulamasi yapilandirilmamis" in stderr
    assert not (tmp_path / "Caddyfile.rendered").exists()
