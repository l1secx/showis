"""Sahte/spam/atıl hesap (authenticity) skoru — ROADMAP 4.1.

Güven skorundan **bağımsız** ikinci bir sinyal: "bu hesap gerçekten kullanılıyor mu,
yoksa boş/bot/terk edilmiş mi?" Güven skoru (resolver) "bu hesap aradığımız kişi mi?"
sorusunu yanıtlar; authenticity "bu hesap canlı bir insan izi taşıyor mu?" sorusunu.

Tasarım ilkeleri:

1. **Deterministik**: ağ isteği yok, LLM yok, saat dışında girdi yok (``now`` enjekte
   edilebilir). Aynı nitelikler → aynı skor.
2. **Eksik veri cezalandırılmaz**: connector'ların çoğu alan göndermiyor. Yalnız
   *mevcut* sinyaller ağırlıklandırılır; hiç sinyal yoksa skor **0.5** (nötr).
3. **Kanıt zayıfsa iddia da zayıf**: elde edilen toplam ağırlık, nötr önselden (0.5)
   uzaklaşma miktarını sınırlar (``_CONFIDENCE_FULL_WEIGHT``).
4. **Açıklanabilirlik**: her kullanılan sinyal ``reasons`` içinde Türkçe olarak yazılır;
   bu liste bulgu açıklamasına (deterministik) eklenir.

Sinyaller ve ağırlıkları (toplam 1.10):

===============  =======  ====================================================
sinyal           ağırlık  ölçüt
===============  =======  ====================================================
``account_age``    0.30   hesap yaşı: <7 gün 0.05 … ≥2 yıl 1.00
``activity``       0.30   repo/post/package/karma/follower toplamı (log ölçek)
``bio``            0.15   boş 0.10 … ≥50 karakter 1.00
``avatar``         0.15   yok/varsayılan 0.10-0.20 … gerçek görsel 1.00
``name``           0.10   görünen ad boş 0.10 … dolu 1.00
``cross_platform`` 0.10   ≥3 platformda aynı kullanıcı adı + tümü boş → 0.00
===============  =======  ====================================================
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("showis.authenticity")

#: Hiç sinyal yoksa dönen nötr değer
NEUTRAL_SCORE = 0.5

#: Tamamen boş profilin üst sınırı (hesap yaşı boşluğu aklamaz)
EMPTY_PROFILE_CAP = 0.30

#: Bu ağırlık toplamına ulaşınca önselden tamamen uzaklaşılır (güven doygunluğu)
_CONFIDENCE_FULL_WEIGHT = 0.60

#: Sinyal ağırlıkları (toplam 1.10 — dokümantasyon tablosuyla aynı)
WEIGHTS: dict[str, float] = {
    "account_age": 0.30,
    "activity": 0.30,
    "bio": 0.15,
    "avatar": 0.15,
    "name": 0.10,
    "cross_platform": 0.10,
}

#: Varsayılan/üretilmiş avatar işaretleri (URL parçası)
_DEFAULT_AVATAR_MARKERS = (
    "identicon",
    "ui-avatars",
    "ui_avatars",
    "default_avatar",
    "avatar-default",
    "no-avatar",
    "noavatar",
    "blank.png",
    "d=mp",  # gravatar "mystery person"
    "d=404",
    "letter_avatar",
    "letter-avatar",
)

#: Aktivite sayılabilecek nitelikler -> ölçek katsayısı
_ACTIVITY_KEYS: dict[str, float] = {
    "public_repos": 1.0,
    "repos": 1.0,
    "repos_count": 1.0,
    "post_count": 1.0,
    "posts": 1.0,
    "submissions": 1.0,
    "package_count": 1.0,
    "packages": 1.0,
    "project_count": 1.0,
    "projects": 1.0,
    "gists": 1.0,
    "contributions": 1.0,
    "contribution_count": 1.0,
    "total_karma": 0.01,  # 100 karma = 1 birim
    "karma": 0.01,
    "followers": 0.1,  # 10 takipçi = 1 birim
    "followers_count": 0.1,
}

_DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d.%m.%Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d %b %Y",
    "%d %B %Y",
)

_USERNAME_SPLIT = re.compile(r"[._\-\s]+")


@dataclass(frozen=True)
class AuthenticityResult:
    """Authenticity çıktısı: 0..1 skor + insan-okur nedenler."""

    score: float
    reasons: list[str] = field(default_factory=list)
    signals: dict[str, float] = field(default_factory=dict)

    @property
    def is_low(self) -> bool:
        """Açıklamada "düşük güvenilirlik" uyarısı eşiği."""
        return self.score < 0.35

    @property
    def used_signals(self) -> list[str]:
        return sorted(self.signals)


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------


def _as_number(value: Any) -> float | None:
    """Sayı/str (``"1.2k"``, ``"123"``) -> float; çözülemezse None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    text = str(value).strip().replace(",", "").replace(" ", "")
    if not text:
        return None
    multiplier = 1.0
    lower = text.lower()
    if lower.endswith("k"):
        multiplier, text = 1000.0, lower[:-1]
    elif lower.endswith("m"):
        multiplier, text = 1_000_000.0, lower[:-1]
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def parse_timestamp(value: Any) -> datetime | None:
    """ISO tarih, epoch saniye/milisaniye veya yaygın metin biçimini çözer."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds <= 0:
            return None
        # > 1e11 -> milisaniye (reddit created_utc saniye, JS ISO ms)
        if seconds > 1e11:
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return parse_timestamp(int(text))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is None:
        for fmt in _DATE_FORMATS:
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_score(days: float) -> float:
    """Hesap yaşı -> 0..1 (ROADMAP: çok yeni hesaplar düşük güven)."""
    if days < 7:
        return 0.05
    if days < 30:
        return 0.25
    if days < 90:
        return 0.45
    if days < 365:
        return 0.65
    if days < 730:
        return 0.80
    return 1.0


def _age_label(days: float) -> str:
    if days < 7:
        return f"hesap {int(days)} günlük (çok yeni)"
    if days < 30:
        return f"hesap {int(days)} günlük (yeni)"
    if days < 365:
        return f"hesap {int(days // 30)} aylık"
    return f"hesap {days / 365:.1f} yıllık"


def _activity_score(value: float) -> float:
    """Toplam etkinlik -> 0..1 (log ölçek; hiç etkinlik yoksa 0.02)."""
    if value <= 0:
        return 0.02
    return min(1.0, 0.05 + 0.30 * math.log10(1.0 + value))


def _bio_score(bio: Any) -> float:
    if bio is None:
        return 0.10
    text = str(bio).strip()
    if not text:
        return 0.10
    if len(text) < 10:
        return 0.55  # kısa ama gerçek bio: zayıf pozitif (boş bırakmaktan iyi)
    if len(text) < 50:
        return 0.75
    return 1.0


def _is_default_avatar(url: Any) -> bool:
    text = str(url or "").strip().lower()
    if not text:
        return True
    return any(marker in text for marker in _DEFAULT_AVATAR_MARKERS)


def _avatar_score(url: Any) -> float:
    if _is_default_avatar(url):
        return 0.10 if not str(url or "").strip() else 0.20
    return 1.0


def _activity_total(attrs: dict[str, Any]) -> tuple[float, list[str]] | None:
    """Mevcut aktivite anahtarlarını ağırlıklı toplar; hiç yoksa None."""
    total = 0.0
    used: list[str] = []
    for key, factor in _ACTIVITY_KEYS.items():
        if key not in attrs:
            continue
        raw = attrs.get(key)
        if isinstance(raw, (list, tuple, set)):
            count = float(len(raw))
        else:
            number = _as_number(raw)
            if number is None:
                continue
            count = number
        used.append(key)
        total += max(0.0, count) * factor
    if not used:
        return None
    return total, used


def _is_empty_profile(attrs: dict[str, Any]) -> bool:
    """Profilde insan izi var mı? (cross-platform Sybil kontrolü için)."""
    if str(attrs.get("name") or "").strip():
        return False
    if str(attrs.get("bio") or "").strip():
        return False
    if not _is_default_avatar(attrs.get("avatar_url")):
        return False
    activity = _activity_total(attrs)
    return activity is None or activity[0] <= 0


# ---------------------------------------------------------------------------
# Genel API
# ---------------------------------------------------------------------------


def compute_authenticity(
    attributes: dict[str, Any],
    *,
    platforms_with_same_username: int = 1,
    now: datetime | None = None,
) -> AuthenticityResult:
    """Profil niteliklerinden 0..1 authenticity skoru ve nedenleri üretir.

    Args:
        attributes: Connector'ın entity nitelikleri (created_at, bio, avatar_url,
            public_repos, karma, followers, name …).
        platforms_with_same_username: Aynı kullanıcı adının görüldüğü platform sayısı
            (≥3 ve tüm profiller boşsa Sybil şüphesi).
        now: Testler için saat enjeksiyonu (varsayılan: şimdi/UTC).
    """
    attrs = attributes or {}
    reasons: list[str] = []
    signals: dict[str, float] = {}

    # 1) Hesap yaşı
    created = parse_timestamp(
        attrs.get("created_at") or attrs.get("joined_at") or attrs.get("created")
    )
    if created is not None:
        reference = now or datetime.now(timezone.utc)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        days = (reference - created).total_seconds() / 86400.0
        days = max(0.0, days)  # gelecek tarih = saat kayması; en kötü varsay
        signals["account_age"] = _age_score(days)
        reasons.append(_age_label(days))
    elif any(k in attrs for k in ("created_at", "joined_at", "created")):
        reasons.append("hesap tarihi okunamadı (yaş sinyali atlandı)")

    # 2) Aktivite
    activity = _activity_total(attrs)
    if activity is not None:
        total, used_keys = activity
        signals["activity"] = _activity_score(total)
        if total <= 0:
            reasons.append(f"etkinlik yok ({', '.join(sorted(used_keys))} = 0)")
        else:
            reasons.append(
                f"etkinlik toplamı {total:g} ({', '.join(sorted(used_keys))})"
            )

    # 3) Bio
    if "bio" in attrs:
        signals["bio"] = _bio_score(attrs.get("bio"))
        if not str(attrs.get("bio") or "").strip():
            reasons.append("bio boş")

    # 4) Avatar
    if "avatar_url" in attrs or "avatar" in attrs:
        avatar_url = attrs.get("avatar_url") or attrs.get("avatar")
        signals["avatar"] = _avatar_score(avatar_url)
        if _is_default_avatar(avatar_url):
            reasons.append(
                "avatarsız" if not str(avatar_url or "").strip() else "varsayılan avatar"
            )

    # 5) Görünen ad
    if "name" in attrs:
        name_text = str(attrs.get("name") or "").strip()
        signals["name"] = 1.0 if len(name_text) >= 2 else 0.10
        if not name_text:
            reasons.append("görünen ad yok")

    # 6) Çapraz platform tutarlılığı (Sybil/atıl çoklu hesap)
    if platforms_with_same_username >= 3:
        if _is_empty_profile(attrs):
            signals["cross_platform"] = 0.0
            reasons.append(
                f"{platforms_with_same_username} platformda aynı kullanıcı adı, tüm profiller boş"
            )
        else:
            signals["cross_platform"] = 0.70
            reasons.append(
                f"{platforms_with_same_username} platformda aynı kullanıcı adı (profiller dolu)"
            )

    if not signals:
        # Eksik veri cezalandırılmaz (nötr), ancak hangi sinyalin okunamadığı yazılır.
        return AuthenticityResult(
            score=NEUTRAL_SCORE,
            reasons=[*reasons, "yetersiz veri (nötr)"],
            signals={},
        )

    available = sum(WEIGHTS[key] for key in signals)
    weighted_mean = sum(signals[key] * WEIGHTS[key] for key in signals) / available
    confidence = min(1.0, available / _CONFIDENCE_FULL_WEIGHT)
    score = NEUTRAL_SCORE + (weighted_mean - NEUTRAL_SCORE) * confidence

    # Eski ama tamamen boş profil (isim/bio/avatar/etkinlik yok) "terk edilmiş"tir:
    # hesap yaşı tek başına bu boşluğu aklamaz.
    if _is_empty_profile(attrs) and not str(attrs.get("name") or "").strip():
        score = min(score, EMPTY_PROFILE_CAP)
        reasons.append("profil tamamen boş (isim, bio, avatar, etkinlik yok)")

    score = round(max(0.0, min(1.0, score)), 2)

    reasons.append(f"kullanılan sinyaller: {', '.join(sorted(signals))}")
    return AuthenticityResult(score=score, reasons=reasons, signals=signals)


def username_components(username: str) -> list[str]:
    """Kullanıcı adı parçaları (ileride isim tutarlılığı için yardımcı)."""
    return [part for part in _USERNAME_SPLIT.split(username or "") if part]
