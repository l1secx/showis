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
    vset = generate_variants("Ali", "Kaya", hints={"nickname": "darkcoder"})
    variants = [v.username for v in vset.variants]

    assert "darkcoder" in variants
    assert "darkcoder99" in variants
    assert "ali.kaya" in variants
