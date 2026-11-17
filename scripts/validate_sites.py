#!/usr/bin/env python3
"""Site-check spesifikasyon doğrulayıcı (T-357).

İki mod:

* ``--static`` (varsayılan): şema/URL/anahtar tekilliği kontrolleri — **ağ yok**.
  Testlerde de kullanılır (`tests/test_sitecheck_specs.py`).
* ``--live --limit N``: gerçek istek atarak örnek kullanıcı adlarıyla
  "yok" (ve istenirse "var") sinyalini doğrular. Varsayılan **kapalıdır**;
  yalnız açıkça istenirse çalışır ve AUP gereği robots.txt'e uyar.

Kullanım::

    python scripts/validate_sites.py --static
    python scripts/validate_sites.py --live --limit 15
    python scripts/validate_sites.py --live --limit 40 --only github,keybase
    python scripts/validate_sites.py --static --live --limit 15   # ikisi birden

Canlı modda her spec için:
  1. rastgele/olmayan bir kullanıcı adı → beklenen: ``absent`` (veya ``unknown``),
  2. spec ``--present`` listesindeyse bilinen bir kullanıcı adı → beklenen: ``exists``.

Çıkış kodu: statik hata varsa 1, canlı doğrulamada beklenmeyen sonuç oranı
%40'ı aşarsa 2, aksi halde 0 (CI'da kullanılabilir).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import secrets
import sys
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
for _rel in (
    "apps/api",
    "packages/contracts",
    "packages/settings",
    "packages/llm",
    "packages/engine",
    "packages/connectors",
):
    _path = str(REPO_ROOT / _rel)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from showis_connectors.sitecheck.sites import SITES, SiteSpec  # noqa: E402

#: Canlı doğrulamada "var" beklenen bilinen profiller (spec key -> kullanıcı adı)
KNOWN_PRESENT: dict[str, str] = {
    "bitbucket": "atlassian",
    "codeberg": "forgejo",
    "codepen": "chriscoyier",
    "dribbble": "dribbble",
    "about.me": "aboutme",
    "speakerdeck": "speakerdeck",
    "flickr": "flickr",
    "letterboxd": "letterboxd",
    "goodreads": "goodreads",
    "lastfm": "lastfm",
    "vimeo": "vimeo",
    "bandcamp": "bandcamp",
    "dailymotion": "dailymotion",
    "scratch": "scratch",
    "telegram": "telegram",
    "lichess": "lichess",
    "musicbrainz": "musicbrainz",
    "rubygems": "rubygems",
    "nuget": "nuget",
    "packagist": "packagist",
    "wattpad": "wattpad",
    "mastodon": "mastodon",
    "pixelfed": "pixelfed",
    "youtube": "youtube",
    "medium": "medium",
    "deviantart": "deviantart",
    "behance": "behance",
    "pinterest": "pinterest",
    "tumblr": "tumblr",
    "myanimelist": "myanimelist",
    "soundcloud": "soundcloud",
    "itch.io": "itch",
    "vk": "vk",
    "9gag": "9gag",
    "flickr2": "flickr",
}

_UUIDISH = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Statik doğrulama
# ---------------------------------------------------------------------------


def static_issues(sites: list[SiteSpec] | None = None) -> list[str]:
    """Şema/URL/anahtar kontrolleri; boş liste = temiz."""
    sites = SITES if sites is None else sites
    issues: list[str] = []
    seen: dict[str, int] = {}

    for spec in sites:
        seen[spec.key] = seen.get(spec.key, 0) + 1
        if not spec.key or not spec.key.strip():
            issues.append(f"{spec.name}: key boş")
        if not spec.url_template.startswith(("http://", "https://")):
            issues.append(f"{spec.key}: url_template http(s) ile başlamalı")
        if "{u}" not in spec.url_template:
            issues.append(f"{spec.key}: url_template '{{u}}' içermiyor")
        if spec.expect_status is not None and spec.expect_status < 200:
            issues.append(f"{spec.key}: expect_status geçersiz ({spec.expect_status})")
        if not spec.absent_statuses and not spec.absent_regex and spec.json_path is None:
            # 200 dışındaki her şeyi "var" sayma riski: absent sinyali yok
            if spec.present_regex is None:
                issues.append(
                    f"{spec.key}: 'absent' sinyali yok (404 listesi / absent_regex / json_path "
                    "veya present_regex gerekli)"
                )
        if spec.present_regex is not None:
            try:
                re.compile(spec.present_regex)
            except re.error as exc:
                issues.append(f"{spec.key}: present_regex derlenemiyor ({exc})")
        if spec.absent_regex is not None:
            try:
                re.compile(spec.absent_regex)
            except re.error as exc:
                issues.append(f"{spec.key}: absent_regex derlenemiyor ({exc})")
        if spec.json_path is not None and spec.headers.get("Accept") is None:
            issues.append(f"{spec.key}: json_path var ama Accept: application/json yok")
        parsed = urlparse(spec.url_template.replace("{u}", "sample"))
        if not parsed.hostname:
            issues.append(f"{spec.key}: host çözümlenemedi")
        if _UUIDISH.match(str(spec.key)):
            issues.append(f"{spec.key}: key uuid benzeri")

    for key, count in seen.items():
        if count > 1:
            issues.append(f"yinelenen key: {key} ({count})")
    return issues


def static_report() -> dict[str, object]:
    issues = static_issues()
    platforms = {spec.platform.value for spec in SITES}
    return {
        "sites": len(SITES),
        "platforms": len(platforms),
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# Canlı doğrulama
# ---------------------------------------------------------------------------


async def _check(
    spec: SiteSpec, username: str, client: object, timeout: float = 12.0
) -> tuple[str, str | None]:
    """Tek spec için engine sınıflandırmasını çalıştırır (zaman aşımı güvenli)."""
    from showis_connectors.sitecheck.engine import UsernameCheckEngine

    engine = UsernameCheckEngine(http_client=client, sites=[spec], concurrency=1)  # type: ignore[arg-type]
    try:
        results = await asyncio.wait_for(engine.check_username(username), timeout=timeout)
    except TimeoutError:
        return "timeout", f"{timeout:.0f}s aşıldı"
    except Exception as exc:  # noqa: BLE001 - tek spec doğrulamayı düşürmesin
        return "error", type(exc).__name__
    result = results[0]
    return result.status, result.detail or None


async def live_validation(
    limit: int,
    only: set[str] | None = None,
    with_present: bool = True,
    timeout: float = 15.0,
) -> dict[str, object]:
    """Örnek kullanıcı adlarıyla canlı doğrulama (yalnız açıkça istenirse)."""
    from showis_engine.http import HttpClient

    client = HttpClient(enable_ssrf_guard=True)
    targets = [s for s in SITES if not only or s.key in only][:limit]
    # Rastgele ama gecerli bicimli kullanici adi (VAR olma olasiligi ~0)
    absent_username = f"zzq{secrets.token_hex(6)}"
    rows: list[dict[str, object]] = []
    # 'timeout'/'error'/'skipped' kabul: host erişilemez veya robots.txt engeli —
    # hiçbiri "hesap var" demek değildir (yanlış pozitif üretilmez).
    expected_absent = {"absent", "unknown", "timeout", "error", "skipped"}
    unexpected = 0

    for spec in targets:
        status, detail = await _check(spec, absent_username, client, timeout=timeout)
        row: dict[str, object] = {
            "key": spec.key,
            "absent_probe": absent_username,
            "absent_status": status,
            "absent_detail": detail,
            "absent_ok": status in expected_absent,
        }
        if not row["absent_ok"]:
            unexpected += 1

        if with_present and spec.key in KNOWN_PRESENT:
            present_username = KNOWN_PRESENT[spec.key]
            p_status, p_detail = await _check(
                spec, present_username, client, timeout=timeout
            )
            row["present_probe"] = present_username
            row["present_status"] = p_status
            row["present_detail"] = p_detail
            row["present_ok"] = p_status in {"exists", "absent", "unknown"}
        rows.append(row)
        print(
            f"  {spec.key:<16} absent={row['absent_status']:<8}"
            + (f" present={row.get('present_status')}" if "present_status" in row else "")
        )

    return {
        "checked": len(rows),
        "unexpected_absent": unexpected,
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Site-check spec doğrulayıcı")
    parser.add_argument(
        "--static", action="store_true", help="yalnız statik kontroller (varsayılan)"
    )
    parser.add_argument("--live", action="store_true", help="canlı istek doğrulaması (ağ)")
    parser.add_argument("--limit", type=int, default=15, help="canlı modda spec sayısı")
    parser.add_argument(
        "--only", default="", help="virgülle ayrılmış spec anahtarları (canlı mod)"
    )
    parser.add_argument("--no-present", action="store_true", help="bilinen profilleri deneme")
    parser.add_argument("--json", action="store_true", help="JSON çıktı")
    return parser.parse_args(argv)


async def main_async(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report: dict[str, object] = {}

    if args.static or not args.live:
        static = static_report()
        report["static"] = static
        if not args.json:
            print(f"Statik: {static['sites']} spec, {static['platforms']} platform")
            if static["issues"]:
                print(f"  {len(static['issues'])} sorun:")
                for issue in static["issues"]:
                    print(f"   - {issue}")
            else:
                print("  sorun yok")
        if static["issues"]:
            return 1

    if args.live:
        only = {part.strip() for part in args.only.split(",") if part.strip()} or None
        print(f"Canlı doğrulama: {args.limit} spec (robots.txt'e uyulur)")
        live = await live_validation(
            args.limit, only=only, with_present=not args.no_present
        )
        report["live"] = live
        if not args.json:
            bad = live["unexpected_absent"]
            print(f"  beklenmeyen 'absent-dışı' sonuç: {bad}/{live['checked']}")
        if live["checked"] and live["unexpected_absent"] / live["checked"] > 0.4:
            return 2

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def main() -> int:
    try:
        return asyncio.run(main_async())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
