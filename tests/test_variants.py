from showis_engine.variants import generate_variants, normalize_turkish


def test_turkish_normalization_edge_cases():
    assert normalize_turkish("Şule Öztürk") == "suleozturk"
    assert normalize_turkish("Çağrı Çelik") == "cagricelik"
    assert normalize_turkish("İbrahim Işık") == "ibrahimisik"
    assert normalize_turkish("Gökçe Yağız") == "gokceyagiz"


def test_variants_generation_patterns():
    vset = generate_variants("Ahmet", "Yılmaz", hints={"year": "1995"})
    variants = [v.username for v in vset.variants]

    assert "ahmet.yilmaz" in variants
    assert "ahmetyilmaz" in variants
    assert "ahmet_yilmaz" in variants
    assert "a.yilmaz" in variants
    assert "ahmetyilmaz1995" in variants
    assert len(vset.variants) <= 60


def test_variants_with_nickname():
    vset = generate_variants("Ali", "Kaya", nickname="darkcoder")
    variants = [v.username for v in vset.variants]

    assert "darkcoder" in variants
    assert "darkcoder99" in variants
    assert "ali.kaya" in variants


def test_handle_only_target_produces_nick_variants():
    vs = generate_variants("", "", {}, nickname="kerem")
    names = [v.username for v in vs.variants]
    assert names[0] == "kerem" and vs.variants[0].pattern == "manual"
    assert vs.name_forms == []
    assert not any(v.pattern in {"first", "last", "first.last"} for v in vs.variants)


def test_nickname_is_normalized_for_matching():
    vs = generate_variants("", "", {}, nickname="@Kerem")
    assert vs.variants[0].username == "kerem"


def test_uppercase_nickname_produces_lowercase_variant():
    vs = generate_variants("", "", {}, nickname="KEREM")
    assert vs.variants[0].username == "kerem"


def test_hints_username_no_longer_creates_variants():
    vs = generate_variants("", "", {"username": "kerem"})
    assert vs.variants == []


def test_name_mode_output_is_unchanged():
    # Nick bloğu (exact + tahminler) isim desenlerinden ÖNCE gelir; tahminler
    # planner/connector dilim bütçelerinin ([:10], [:15], max_usernames) içinde kalsın.
    vs = generate_variants("Kerem", "Boz", {"city": "Ankara"}, nickname="kb")
    assert [v.username for v in vs.variants][:3] == ["kb", "kb99", "kb01"]


def test_nickname_block_stays_inside_consumer_slices():
    # planner `[:10]`/`[:15]`, publicapi/sitecheck `max_usernames` ile dilimliyor:
    # nick tahminleri bu pencerelerin dışına düşmemeli.
    vs = generate_variants("Kerem", "Boz", {"year": "1995"}, nickname="kb")
    first10 = {v.username for v in vs.variants[:10]}
    assert {"kb", "kb1995", "kb99", "kb01", "kb_dev", "kb.dev"} <= first10


def test_none_nickname_does_not_crash():
    # DB'den nullable okunan nickname None gelebilir (handle-only hedefte değil,
    # eski satırlarda) — üretici çökmemeli.
    vs = generate_variants("Kerem", "Boz", {}, nickname=None)  # type: ignore[arg-type]
    assert "kerem.boz" in [v.username for v in vs.variants]
