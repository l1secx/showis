#!/usr/bin/env python3
"""Eval seti — 30 sentetik vakayla aday siralama precision@3 olcumu (S4-4).

Her vaka: hedef ad/soyad + ipuclari + beklenen (platform, username) cifti.
Resolver'a sentetik entity'ler verilir, top-3 bulgunun kac tanesinin
beklenen hesap oldugu olculur. Gercek kisi verisi KULLANILMAZ.
Calistirma: make eval
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages/contracts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages/engine"))

from showis_contracts import Entity, EntityType, Platform, Target
from showis_engine.resolver import Resolver

# (first, last, hints, expected [(platform, username)], distractor entities)
# Her vaka icin: beklenen hesaplar yuksek sinyalli, distractor'lar rastgele.
CASES: list[dict] = []

NAMES = [
    ("Ahmet", "Yilmaz", {"city": "Ankara", "company": "CyberSec"}),
    ("Selin", "Kara", {"city": "Istanbul"}),
    ("Can", "Demir", {"city": "Izmir", "company": "NetCorp"}),
    ("Elif", "Sahin", {"city": "Bursa"}),
    ("Mert", "Celik", {"city": "Antalya", "company": "SecLab"}),
    ("Zeynep", "Arslan", {"city": "Adana"}),
    ("Emre", "Koc", {"city": "Konya", "company": "DataWorks"}),
    ("Deniz", "Yildiz", {"city": "Gaziantep"}),
    ("Burak", "Ozturk", {"city": "Mersin", "company": "CloudNine"}),
    ("Gamze", "Aydin", {"city": "Kayseri"}),
]

# Her isim icin 3 varyasyon = 30 vaka
VARIANTS = [
    {"suffix": "", "name_key": "full"},
    {"suffix": "dev", "name_key": "nick"},
    {"suffix": "99", "name_key": "digits"},
]


def _norm(s: str) -> str:
    tr = str.maketrans({"ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ş": "s", "ü": "u"})
    return s.lower().translate(tr)


def build_cases() -> list[dict]:
    cases = []
    for first, last, hints in NAMES:
        fn, ln = _norm(first), _norm(last)
        for i, var in enumerate(VARIANTS):
            if var["name_key"] == "full":
                user = f"{fn}{ln}"
                disp = f"{first} {last}"
            elif var["name_key"] == "nick":
                user = f"{fn}_dev"
                disp = f"{first} {last}"
            else:
                user = f"{fn}{ln}99"
                disp = f"{first} {last}"

            expected = [
                (Platform.GITHUB, user),
                (Platform.TRYHACKME, user),
                (Platform.HACKTHEBOX, user),
            ]
            # Birkac distractor entity (benzer ama farkli)
            distractors = [
                (Platform.GITHUB, f"{fn}{ln}x"),
                (Platform.TRYHACKME, f"{fn[:3]}{ln}"),
                (Platform.WEB, "ornek-site"),
            ]
            cases.append(
                {
                    "first": first,
                    "last": last,
                    "hints": hints,
                    "expected": expected,
                    "user": user,
                    "display": disp,
                    "distractors": distractors,
                    "variant_idx": i,
                }
            )
    return cases


def run_eval() -> dict:
    resolver = Resolver()
    results = []
    for case in build_cases():
        target = Target(
            first_name=case["first"],
            last_name=case["last"],
            hints=case["hints"],
        )
        entities: list[Entity] = []
        user = case["user"]
        # Beklenen hesaplar: ayni username 3 platformda + isim/location eslesmesi
        for platform, uname in case["expected"]:
            entities.append(
                Entity(
                    id=f"{platform.value}:{uname}",
                    type=EntityType.ACCOUNT,
                    platform=platform,
                    label=uname,
                    url=f"https://{platform.value}.example/{uname}",
                    attributes={
                        "name": case["display"],
                        "location": case["hints"].get("city", ""),
                        "company": case["hints"].get("company"),
                    },
                )
            )
        for platform, uname in case["distractors"]:
            entities.append(
                Entity(
                    id=f"{platform.value}:{uname}",
                    type=EntityType.ACCOUNT,
                    platform=platform,
                    label=uname,
                    attributes={"name": "Baska Kisi"},
                )
            )

        expected_set = {(p.value, u) for p, u in case["expected"]}
        scored = []
        for ent in entities:
            finding = resolver.resolve_entity(
                ent,
                target,
                case_id="eval",
                scan_id="eval",
                username_platform_count=3 if ent.label == user else 1,
            )
            scored.append((finding.score, ent.platform.value, ent.label))

        scored.sort(key=lambda x: -x[0])
        top3 = scored[:3]
        hits = sum(1 for _, p, u in top3 if (p, u) in expected_set)
        precision_at_3 = hits / 3.0
        results.append(
            {
                "case": f"{case['first']} {case['last']} #{case['variant_idx']}",
                "expected_user": user,
                "top3": [(p, u, round(s, 2)) for s, p, u in top3],
                "precision@3": precision_at_3,
            }
        )

    mean_p3 = sum(r["precision@3"] for r in results) / len(results) if results else 0.0
    return {"cases": len(results), "mean_precision@3": round(mean_p3, 4), "results": results}


def main() -> int:
    report = run_eval()
    print(f"Eval: {report['cases']} vaka")
    print(f"Ortalama precision@3: {report['mean_precision@3']}")
    for r in report["results"]:
        print(
            f"  {r['case']:28s} user={r['expected_user']:20s} "
            f"p@3={r['precision@3']:.2f} top3={r['top3']}"
        )
    # Kabul esigi: sentetik sette yuksek precision beklenir
    return 0 if report["mean_precision@3"] >= 0.8 else 1


if __name__ == "__main__":
    sys.exit(main())
