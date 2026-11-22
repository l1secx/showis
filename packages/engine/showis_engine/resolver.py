import re
from difflib import SequenceMatcher

from showis_contracts import (
    ConfidenceBreakdown,
    Entity,
    Finding,
    FindingStatus,
    Target,
    make_finding_id,
)

from showis_engine.authenticity import compute_authenticity
from showis_engine.variants import normalize_turkish

_NAME_SPLIT_RE = re.compile(r"[\s._\-]+")


def _name_tokens(value: str) -> list[str]:
    """İsim parçaları: Türkçe katlanmış, noktalama/alt çizgi/tireden ayrılmış."""
    tokens: list[str] = []
    for part in _NAME_SPLIT_RE.split(str(value or "")):
        token = normalize_turkish(part)
        if token:
            tokens.append(token)
    return tokens


def _token_overlap(left: list[str], right: list[str]) -> float:
    """Sıra bağımsız isim örtüşmesi (0..1); baş harf kısaltmasını da kabul eder."""
    if not left or not right:
        return 0.0
    remaining = list(right)
    matched = 0
    for token in left:
        exact = token in remaining
        if exact:
            remaining.remove(token)
            matched += 1
            continue
        # "a." / "a" gibi baş harf, tam adın başlangıcıysa eşleşir; ters yönde
        # de aday baş harf olabilir ("Ahmet" ~ "A.").
        initial_match = None
        for candidate in remaining:
            if len(token) == 1 and candidate.startswith(token):
                initial_match = candidate
                break
            if len(candidate) == 1 and token.startswith(candidate):
                initial_match = candidate
                break
        if initial_match is not None:
            remaining.remove(initial_match)
            matched += 1
    return matched / max(len(left), len(right))


def calculate_name_similarity(target_fullname: str, candidate_name: str | None) -> float:
    """Ad-soyad benzerliğini hesaplar (≥0.92 -> 0.20 tam puan, altı lineer).

    Sıra bağımsızdır: `Ahmet Yılmaz` ile `Yılmaz Ahmet` tam eşleşir. İkinci ad,
    baş harf (`A. Yılmaz`) ve profildeki unvan/ek metinler kısmi puan alır.
    """
    if not candidate_name:
        return 0.0
    s1 = normalize_turkish(target_fullname)
    s2 = normalize_turkish(candidate_name)
    if not s1 or not s2:
        return 0.0

    # 1) Karakter benzerliği (tam eşleşme ve yakın yazımlar)
    ratio = SequenceMatcher(None, s1, s2).ratio()
    if ratio >= 0.92:
        return 0.20

    # 2) Kelime tabanlı: sıra bağımsız tam eşleşme (ad-soyad ters yazılmış olabilir)
    t1 = _name_tokens(target_fullname)
    t2 = _name_tokens(candidate_name)
    overlap = _token_overlap(t1, t2)
    token_score = 0.20 * overlap
    if len(t1) >= 2 and len(t2) >= 2 and overlap >= 0.999:
        return 0.20

    # 3) Ad-soyad tam alt küme olarak geçiyorsa (ör. "Ahmet Yılmaz (Developer)")
    subset_score = 0.0
    if (s1 in s2 or s2 in s1) and len(s1) >= 4 and len(s2) >= 4:
        subset_overlap = min(len(s1), len(s2)) / max(len(s1), len(s2))
        if subset_overlap >= 0.60:
            subset_score = max(0.14, 0.20 * subset_overlap)

    best = max(token_score, subset_score)
    if best > 0:
        return round(min(0.20, best), 2)

    if ratio <= 0.50:
        return 0.0
    # 0.50 - 0.92 arası lineer ölçeklendirme
    return round(0.20 * ((ratio - 0.50) / 0.42), 2)


class Resolver:
    def __init__(self):
        pass

    @staticmethod
    def detect_contradiction(target: Target, attributes: dict) -> bool:
        """Hedef ipuçları ile aday profili arasındaki belirgin çelişkileri tespit eder."""
        target_hints = target.hints or {}
        target_city = normalize_turkish(str(target_hints.get("city") or ""))
        candidate_loc = normalize_turkish(str(attributes.get("location") or ""))
        if target_city and candidate_loc:
            tr_cities = {"istanbul", "ankara", "izmir", "bursa", "antalya", "turkey", "turkiye"}
            if target_city in tr_cities:
                conflicting = {"tokyo", "beijing", "moscow", "seoul", "sao paulo", "brasilia"}
                if any(loc in candidate_loc for loc in conflicting):
                    return True
        return False

    def resolve_entity(
        self,
        entity: Entity,
        target: Target,
        case_id: str,
        scan_id: str,
        *,
        username_platform_count: int = 0,
        known_platforms_with_username: set[str] | None = None,
        has_cross_link: bool = False,
        avatar_match: bool = False,
        email_or_pgp_match: bool = False,
        has_contradiction: bool = False,
        authenticity_score: float | None = None,
    ) -> Finding:
        breakdown = ConfidenceBreakdown()
        attrs = entity.attributes

        # 1. username_exact_match (0.30): aynı kullanıcı adı en az 2 platformda birebir
        if username_platform_count >= 2 or (
            known_platforms_with_username and len(known_platforms_with_username) >= 2
        ):
            breakdown.username_exact_match = 0.30

        # 2. handle_exact (0.15): tek platformda birebir handle eşleşmesi.
        #    Yalnız `username_platform_count == 1` için verilir; çok platformlu
        #    eşleşmenin kanıtı zaten `username_exact_match`'te — aynı kanıt iki
        #    sinyal olarak sayılmaz. Handle `attributes["username"]`'de olmayabilir
        #    (GitHub profil entity'leri yalnız `label` taşır), o yüzden `label`
        #    yedeği kullanılır.
        if breakdown.username_exact_match == 0 and username_platform_count == 1:
            target_handle = normalize_turkish(target.nickname)
            candidate_handle = normalize_turkish(
                str(attrs.get("username") or entity.label or "")
            )
            if target_handle and target_handle == candidate_handle:
                breakdown.handle_exact = 0.15

        # 3. name_similarity (0.20)
        target_fullname = f"{target.first_name} {target.last_name}"
        candidate_name = attrs.get("name") or attrs.get("search_title")
        breakdown.name_similarity = calculate_name_similarity(target_fullname, candidate_name)

        # 4. cross_link (0.25)
        if has_cross_link:
            breakdown.cross_link = 0.25

        # 5. bio_location_company (0.15: 0.05 per field)
        bio_matches = 0
        target_hints = target.hints
        if target_hints.get("city") and attrs.get("location"):
            if normalize_turkish(target_hints["city"]) in normalize_turkish(attrs["location"]):
                bio_matches += 1
        if target_hints.get("company") and attrs.get("company"):
            if normalize_turkish(target_hints["company"]) in normalize_turkish(attrs["company"]):
                bio_matches += 1
        if target_hints.get("keyword") and attrs.get("bio"):
            if normalize_turkish(target_hints["keyword"]) in normalize_turkish(attrs["bio"]):
                bio_matches += 1
        # Okul ipucu: okul adi profil metninde geciyorsa (bio/company/education)
        if target_hints.get("school"):
            school = normalize_turkish(str(target_hints["school"]))
            haystack = normalize_turkish(
                " ".join(
                    str(attrs.get(k) or "")
                    for k in ("school", "education", "company", "bio", "search_snippet")
                )
            )
            if school and school in haystack:
                bio_matches += 1
        breakdown.bio_location_company = round(min(bio_matches, 3) * 0.05, 2)

        # 6. commit_email_or_pgp (0.15)
        if email_or_pgp_match:
            breakdown.commit_email_or_pgp = 0.15

        # 7. avatar_exact_hash (0.10)
        if avatar_match:
            breakdown.avatar_exact_hash = 0.10

        # 8. contradiction (-0.20)
        if not has_contradiction and self.detect_contradiction(target, attrs):
            has_contradiction = True
        if has_contradiction:
            breakdown.contradiction = -0.20

        # Total clamped between 0.0 and 1.0
        total_score = (
            breakdown.username_exact_match
            + breakdown.handle_exact
            + breakdown.name_similarity
            + breakdown.cross_link
            + breakdown.bio_location_company
            + breakdown.commit_email_or_pgp
            + breakdown.avatar_exact_hash
            + breakdown.contradiction
        )
        total_score = max(0.0, min(1.0, round(total_score, 2)))
        breakdown.total = total_score

        # Authenticity (ROADMAP 4.1): güven skorundan BAĞIMSIZ ikinci sinyal.
        # Çağıran taraf hesapladıysa onu kullan (skor), nedenler burada üretilir;
        # böylece açıklama deterministik olarak gerekçelendirilebilir.
        auth = compute_authenticity(
            attrs or {}, platforms_with_same_username=max(username_platform_count, 1)
        )
        if authenticity_score is not None:
            try:
                auth_score = max(0.0, min(1.0, round(float(authenticity_score), 2)))
            except (TypeError, ValueError):
                auth_score = auth.score
        else:
            auth_score = auth.score
        auth_reasons = list(auth.reasons)

        # Status threshold: >=0.80 CONFIRMED, 0.50-0.79 CANDIDATE,
        # isim/okul/handle benzerligi olan dusuk skorlar LEAD (ipucu), gerisi REJECTED.
        if total_score >= 0.80:
            status = FindingStatus.CONFIRMED
        elif total_score >= 0.50:
            status = FindingStatus.CANDIDATE
        elif not has_contradiction and (
            breakdown.name_similarity > 0
            or breakdown.bio_location_company > 0
            or breakdown.handle_exact > 0
        ):
            status = FindingStatus.LEAD
        else:
            status = FindingStatus.REJECTED

        return Finding(
            id=make_finding_id(entity.id),
            case_id=case_id,
            scan_id=scan_id,
            entity_id=entity.id,
            platform=entity.platform,
            url=entity.url,
            label=entity.label,
            status=status,
            score=total_score,
            breakdown=breakdown,
            explanation="",
            evidence_ids=entity.evidence_ids,
            authenticity=auth_score,
            authenticity_reasons=auth_reasons,
        )
