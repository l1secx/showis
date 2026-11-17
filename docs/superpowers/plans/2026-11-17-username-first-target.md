# Handle-first Hedef Modeli — Uygulama Planı

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Showis'te vaka yalnızca bir nick ile açılıp taranabilsin; soyad hiçbir yerde zorunlu olmasın; handle modunda sorgular ve connector'lar gerçekten nick ile çalışsın; planner'ın ürettiği platforma özel sorgular tavanlı biçimde gerçek arama görevine dönüşsün.

**Architecture:** `Target` kontratına `kind` ("name" | "handle") ve `nickname` eklenir; DB'de iki kolon + nullable ad/soyad. Kimlik bilgisi connector'lara `ctx.target.nickname` ile taşınır (hints'e aynalama yok). Resolver'a çift sayılmayan `handle_exact` sinyali eklenir. Faz 2'de `QueryPlan`, mod başına tavanlı `TaskSpec` listesine dönüştürülür ve mevcut bütçe kapısına tabi olur.

**Tech Stack:** Python 3.14, Pydantic v2, SQLAlchemy 2 + Alembic, FastAPI, pytest; Next.js 15 + React 19 + TypeScript; Caddy önünde HTTP Basic.

**Spec:** `docs/superpowers/specs/2026-11-17-username-first-target-design.md`

## Global Constraints

- `kind` varsayılanı `"name"` — mevcut istemciler aynı davranır.
- Nick doğrulama: normalize sonrası **≥3 karakter** ve **≥1 alfanümerik**; baştaki `@` temizlenir.
- `handle_exact` = 0.15 ve **yalnız** tek platformda birebir eşleşmede verilir; ≥2 platformda `username_exact_match` 0.30 verilir, `handle_exact` 0.
- Skor eşikleri değişmez: `>=0.80` CONFIRMED, `>=0.50` CANDIDATE.
- Hiçbir connector veya `TaskSpec` boş sorgu ya da `""` (boş tırnak) göndermez.
- Planner tavanı: **O1=0, O2=8, O3=20**.
- `first_name`/`last_name` kontratta `str` + varsayılan `""` (`None` değil); DB'de nullable, sınırda `None → ""`.
- Kullanıcıya görünen metinler Türkçe; kod/identifier İngilizce.
- Testler: `.venv/bin/pytest tests/... -q`. Kapı: `make gate` (`scripts/final_gate.sh`).
- Faz 1 `make gate` yeşil olmadan Faz 2'ye geçilmez; commit'ler ayrı.

## Review Focus

1. `@Kerem`, `KEREM`, `Kerem` biçiminde girilen nick → eşleşme normalize edilmiş olmalı, saklanan değer kullanıcının yazdığı biçimde kalmalı.
2. Aynı nick hem `hints.username` hem `target.nickname` olarak gelirse → tek varyant, çift görev yok.
3. Soyadı boş hedefle rapor/export → `None` veya `"Kerem "` gibi kırpılmamış boşluk görünmemeli.
4. Handle modunda hiç eşleşme yoksa → tarama sessizce "0 görev" ile bitmemeli; log sebebi göstermeli.
5. Planner görevleri ile connector görevleri aynı sonucu üretirse → mükerrer entity/finding yazılmamalı.

---

## FAZ 1 — Kimlik modeli ve handle desteği

### Task 1: Kontrat — `kind`, `nickname`, `display_name`

**Files:**
- Modify: `packages/contracts/showis_contracts/tasks.py:21-27`
- Test: `packages/contracts/tests/test_contracts.py`

**Interfaces:**
- Produces: `Target.kind: Literal["name","handle"] = "name"`, `Target.nickname: str = ""`,
  `Target.display_name -> str` (ad-soyad varsa o, yoksa `@nick`, yoksa `""`).

- [ ] **Step 1: Write the failing test**

```python
def test_target_display_name_prefers_full_name():
    t = Target(first_name="Kerem", last_name="Boz", nickname="kb")
    assert t.display_name == "Kerem Boz"

def test_target_display_name_falls_back_to_nick():
    assert Target(kind="handle", nickname="kerem").display_name == "@kerem"

def test_target_display_name_no_trailing_space_without_surname():
    assert Target(first_name="Kerem", nickname="kb").display_name == "Kerem"

def test_target_defaults_are_backward_compatible():
    t = Target(first_name="A", last_name="B")
    assert t.kind == "name" and t.nickname == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest packages/contracts/tests/test_contracts.py -q`
Expected: FAIL — `Target` has no `nickname`/`kind`/`display_name`.

- [ ] **Step 3: Implement in `tasks.py`**

`kind: Literal["name", "handle"] = "name"`; `nickname: str = ""`; `first_name: str = ""`;
`last_name: str = ""`; `display_name` `@property` → `f"{first_name} {last_name}".strip() or (f"@{nickname}" if nickname else "")`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest packages/contracts/tests/test_contracts.py -q` → PASS

- [ ] **Step 5: Commit**

```bash
git add packages/contracts/showis_contracts/tasks.py packages/contracts/tests/test_contracts.py
git commit -m "feat(contracts): add target kind, nickname and display_name"
```

---

### Task 2: DB modeli ve migration `0007`

**Files:**
- Modify: `apps/api/showis/db/models.py:85-99`
- Create: `apps/api/alembic/versions/0007_target_identity.py`
- Test: `tests/test_target_model.py` (yeni)

**Interfaces:**
- Consumes: Task 1'in alan adları.
- Produces: `targets.nickname VARCHAR(128) NULL`, `targets.kind VARCHAR(16) NOT NULL DEFAULT 'name'`,
  `first_name`/`last_name` nullable.

- [ ] **Step 1: Write the failing test**

```python
def test_target_columns_support_handle_only():
    c = Target.__table__.c
    assert c.nickname.nullable is True
    assert c.first_name.nullable is True and c.last_name.nullable is True
    assert c.kind.nullable is False

def test_target_kind_has_server_default():
    assert Target.__table__.c.kind.server_default.arg == "name"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_target_model.py -q` → FAIL (`nickname` yok).

- [ ] **Step 3: Implement model + migration**

Model: `nickname: Mapped[str | None] = mapped_column(String(128), nullable=True)`,
`kind: Mapped[str] = mapped_column(String(16), nullable=False, server_default="name")`,
`first_name`/`last_name` → `nullable=True`.
Migration `0007_target_identity` (`down_revision = "0006_query_indexes"`): `add_column` ×2,
`alter_column(... nullable=True)` ×2, backfill
`UPDATE targets SET nickname = COALESCE(hints->>'nickname', hints->>'username') WHERE nickname IS NULL AND (hints ? 'nickname' OR hints ? 'username')`;
downgrade: kolonları düşür, NOT NULL'a döndür (boş satırları `''` ile doldur, docstring'e veri kaybı notu).

- [ ] **Step 4: Run test + migration syntax check**

Run: `.venv/bin/pytest tests/test_target_model.py -q` → PASS
Run: `cd apps/api && ../../.venv/bin/alembic heads` → tek head `0007_target_identity`
Run: `cd apps/api && ../../.venv/bin/alembic upgrade head --sql | grep -c "nickname"` → `>= 1`

- [ ] **Step 5: Commit**

```bash
git add apps/api/showis/db/models.py apps/api/alembic/versions/0007_target_identity.py tests/test_target_model.py
git commit -m "feat(db): target nickname + kind columns, nullable names (0007)"
```

---

### Task 3: Varyant üreticisi — `nickname` parametresi

**Files:**
- Modify: `packages/engine/showis_engine/variants.py:41-122`
- Test: `tests/test_variants.py`

**Interfaces:**
- Produces: `generate_variants(first_name: str, last_name: str, hints: dict | None = None, nickname: str = "", max_variants: int = 60) -> VariantSet`
- Davranış: nick tam verilirse `pattern="manual"`, `confidence=1.0`; hints'ten nick **okunmaz**.

- [ ] **Step 1: Write the failing test**

```python
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
    vs = generate_variants("Kerem", "Boz", {"city": "Ankara"}, nickname="kb")
    assert [v.username for v in vs.variants][:3] == ["kb", "kerem.boz", "keremboz"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_variants.py -q` → FAIL (beklenmeyen anahtar sözcük `nickname`).

- [ ] **Step 3: Implement**

`nickname` parametresini ekle; `nickname = normalize_turkish(nickname.lstrip("@"))`;
`hints.get("nickname")/hints.get("username")` okumasını kaldır; `name_forms` üretimini
yalnız `ad`/`soyad` doluysa çalıştır (boşken `[]`).

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_variants.py -q` → PASS

- [ ] **Step 5: Commit**

```bash
git add packages/engine/showis_engine/variants.py tests/test_variants.py
git commit -m "feat(engine): handle-first variant generation"
```

---

### Task 4: API girişi — `TargetInput` doğrulaması

**Files:**
- Modify: `apps/api/showis/api/v1/cases.py:48, 106-133`
- Test: `tests/integration/test_target_identity.py` (yeni)

**Interfaces:**
- Consumes: Task 1 kontratı.
- Produces: `TargetInput(kind, first_name, last_name, nickname, hints)`; `POST /cases` 201/422 matrisi.
- `hints.username` doluysa ve `nickname` boşsa: `nickname = hints.pop("username")`.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.parametrize(
    "target,expected",
    [
        ({"kind": "handle", "nickname": "kerem"}, 201),
        ({"kind": "handle"}, 422),
        ({"kind": "name", "first_name": "Kerem"}, 201),
        ({"kind": "name", "first_name": "Kerem", "last_name": "Boz"}, 201),
        ({"kind": "name", "last_name": "Boz"}, 422),
        ({"kind": "handle", "nickname": "ab"}, 422),
        ({"kind": "handle", "nickname": "__"}, 422),
        ({"kind": "handle", "nickname": "@Kerem"}, 201),
    ],
)
async def test_target_identity_matrix(client, target, expected):
    res = await client.post("/api/v1/cases", json={
        "name": "M", "purpose": "x" * 10, "legal_basis": "self_check",
        "retention_days": 7, "target": target, "seeds": [],
    })
    assert res.status_code == expected, res.text

async def test_hints_username_moves_into_nickname(client):
    res = await client.post("/api/v1/cases", json={
        "name": "M", "purpose": "x" * 10, "legal_basis": "self_check",
        "retention_days": 7,
        "target": {"kind": "name", "first_name": "Kerem", "hints": {"username": "kb"}},
        "seeds": [],
    })
    body = (await client.get(f"/api/v1/cases/{res.json()['id']}")).json()
    assert body["target"]["nickname"] == "kb"
    assert "username" not in body["target"]["hints"]

async def test_nickname_wins_over_hints_username(client):
    """Review Focus #2: iki kaynakta nick varsa tek kimlik kalmalı."""
    res = await client.post("/api/v1/cases", json={
        "name": "M", "purpose": "x" * 10, "legal_basis": "self_check",
        "retention_days": 7,
        "target": {"kind": "handle", "nickname": "kerem",
                   "hints": {"username": "baska"}},
        "seeds": [],
    })
    body = (await client.get(f"/api/v1/cases/{res.json()['id']}")).json()
    assert body["target"]["nickname"] == "kerem"
    assert "username" not in body["target"]["hints"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_target_identity.py -q` → FAIL (`kind` yok).

- [ ] **Step 3: Implement**

`kind: Literal["name","handle"] = "name"`; `nickname: str = ""`; `first_name`/`last_name`
`min_length` kaldırılıp `str = ""`; `@model_validator(mode="after")`:
name modunda `first_name` boşsa hata; handle modunda nickname boşsa hata;
nick `lstrip("@")` sonrası `normalize` (aynı kural `variants.normalize_turkish` ile) ≥3 ve
`any(ch.isalnum())` değilse hata. `hints` doğrulamasından **sonra** taşıma yap.
`guard_target` çağrısı `first_name`/`last_name` yerine `display_name` kullanır (nick de
yaş sinyali taramasına girer).

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/integration/test_target_identity.py tests/integration/test_api_cases.py -q` → PASS

- [ ] **Step 5: Commit**

```bash
git add apps/api/showis/api/v1/cases.py tests/integration/test_target_identity.py
git commit -m "feat(api): accept handle-only targets, surname optional"
```

---

### Task 5: API okuma yolları + kontrat adaptörleri

**Files:**
- Modify: `apps/api/showis/api/v1/cases.py:448-456, 484-500, 738-750, 987-996`
- Modify: `apps/api/showis/services/scanner.py:267-275`
- Test: `tests/integration/test_target_identity.py` (genişlet)

**Interfaces:**
- Produces: `GET /cases`, `GET /cases/{id}`, `GET /cases/{id}/export` target bloğunda `nickname` + `kind`; DB `None → ""` dönüşümü `_load_contract_target` ve `scanner` içinde.

- [ ] **Step 1: Write the failing test**

```python
async def test_detail_list_and_export_expose_identity(client, handle_case_id):
    detail = (await client.get(f"/api/v1/cases/{handle_case_id}")).json()
    assert detail["target"]["kind"] == "handle"
    assert detail["target"]["nickname"] == "kerem"
    listed = (await client.get("/api/v1/cases")).json()["items"]
    assert any(i["target"]["nickname"] == "kerem" for i in listed)
    export = (await client.get(f"/api/v1/cases/{handle_case_id}/export")).json()
    assert export["target"]["nickname"] == "kerem"

def test_contract_target_maps_none_to_empty_string():
    # DB satırı None taşısa bile kontrat "" üretmeli
    row = SimpleNamespace(first_name=None, last_name=None, nickname=None, kind=None,
                          hints={}, review_required=False)
    t = _db_target_to_contract(row)
    assert (t.first_name, t.last_name, t.nickname, t.kind) == ("", "", "", "name")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_target_identity.py -q` → FAIL (anahtar yok).

- [ ] **Step 3: Implement**

Üç yanıt bloğuna `"nickname": target.nickname or ""`, `"kind": target.kind or "name"` ekle;
`_load_contract_target` ve `scanner.py` target kurulumunda
`first_name=db_target.first_name or ""` (aynı şekilde last_name/nickname/kind). Ortak
satır→kontrat dönüşümü tek yardımcıda toplanır (isim: `_db_target_to_contract`).

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/integration/ -q` → PASS

- [ ] **Step 5: Commit**

```bash
git add apps/api/showis/api/v1/cases.py apps/api/showis/services/scanner.py tests/integration/test_target_identity.py
git commit -m "feat(api): expose target kind and nickname in read paths"
```

---

### Task 6: Resolver — `handle_exact` sinyali

**Files:**
- Modify: `packages/contracts/showis_contracts/findings.py:6-14`
- Modify: `packages/engine/showis_engine/resolver.py:77-170`
- Test: `tests/test_resolver.py`

**Interfaces:**
- Consumes: `Target.nickname`.
- Produces: `ConfidenceBreakdown.handle_exact: float = 0.0`; `handle_exact=0.15` **yalnız**
  `username_platform_count == 1` ve profil handle'ı nick ile normalize eşleşiyorsa.
- LEAD koşulu: `name_similarity > 0 or bio_location_company > 0 or handle_exact > 0`.

- [ ] **Step 1: Write the failing test**

```python
def test_single_platform_exact_handle_is_a_lead():
    f = Resolver().resolve_entity(
        entity(platform="github", attrs={"username": "kerem"}),
        target_handle("kerem"), "c", "s", username_platform_count=1,
    )
    assert f.breakdown.handle_exact == 0.15
    assert f.breakdown.username_exact_match == 0.0
    assert f.status == FindingStatus.LEAD

def test_two_platforms_use_username_exact_not_handle_exact():
    f = Resolver().resolve_entity(
        entity(platform="github", attrs={"username": "kerem"}),
        target_handle("kerem"), "c", "s", username_platform_count=2,
    )
    assert f.breakdown.username_exact_match == 0.30
    assert f.breakdown.handle_exact == 0.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_resolver.py -q` → FAIL (`handle_exact` yok).

- [ ] **Step 3: Implement**

Breakdown alanı; resolver'da `target.nickname` normalize edilip `attrs["username"]` ile
karşılaştırılır (`username_platform_count == 1` şartıyla); LEAD dalına `or handle_exact > 0`.
Toplam formülüne `+ breakdown.handle_exact` eklenir.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_resolver.py -q` → PASS (mevcut testler de yeşil)

- [ ] **Step 5: Commit**

```bash
git add packages/contracts/showis_contracts/findings.py packages/engine/showis_engine/resolver.py tests/test_resolver.py
git commit -m "feat(engine): handle_exact scoring signal for handle-only targets"
```

---

### Task 7: Connector'lar handle-farkında

**Files:**
- Modify: `packages/connectors/showis_connectors/sitecheck/connector.py:110-113`
- Modify: `packages/connectors/showis_connectors/rendered.py:111-114, 130-138`
- Modify: `packages/connectors/showis_connectors/publicapi/base.py:160-163`
- Modify: `packages/connectors/showis_connectors/hackthebox.py:129-132`
- Modify: `packages/connectors/showis_connectors/github.py:90-111`
- Modify: `packages/connectors/showis_connectors/websearch/connector.py:64-80`
- Modify: `packages/connectors/showis_connectors/sitecheck/search_connector.py:66-78`
- Test: `tests/test_github.py`, `tests/test_websearch_connector.py`, `tests/test_sitecheck.py`

**Interfaces:**
- Consumes: `generate_variants(..., nickname=ctx.target.nickname)` (Task 3), `Target.kind`.
- Produces: handle modunda `github` → `f"{nick} in:login"`; `websearch` → `[f'"{nick}"', f'"{nick}" <hint>']`; `rendered`/`sitecheck search` → `f'"{nick}" site:<platform>'`. Hiçbir yerde `""` yok.

- [ ] **Step 1: Write the failing tests**

```python
def test_github_handle_mode_uses_login_search():
    specs = asyncio.run(GitHubConnector().plan(ctx(handle_target("kerem"))))
    assert specs[0].params["query"] == "kerem in:login"

def test_websearch_handle_mode_returns_queries():
    conn = WebSearchConnector(search_provider=stub_provider())
    specs = asyncio.run(conn.plan(ctx(handle_target("kerem"))))
    assert specs and specs[0].params["query"] == '"kerem"'

def test_sitecheck_handle_mode_queries_nick():
    specs = asyncio.run(SiteCheckConnector().plan(ctx(handle_target("kerem"))))
    joined = " ".join(str(s.params.get("query") or "") for s in specs)
    assert "kerem" in joined and '""' not in joined

def test_no_connector_emits_empty_quotes():
    for conn in [GitHubConnector(), WebSearchConnector(search_provider=stub_provider())]:
        for spec in asyncio.run(conn.plan(ctx(handle_target("kerem")))):
            assert '""' not in str(spec.params.get("query") or "")

async def test_empty_handle_plan_logs_reason(client, handle_case_id, monkeypatch):
    """Review Focus #4: handle modunda sıfır görev sessizce geçmemeli."""
    from showis.services import scanner as scanner_mod
    logged: list[str] = []
    monkeypatch.setattr(scanner_mod.event_broadcaster, "broadcast",
                        lambda *a, **kw: logged.append(str(kw)))
    # tüm connector plan'ları boş dönecek şekilde monkeypatch'le
    await run_scan(client, handle_case_id, mode="O2")
    assert any("görev üretilemedi" in m for m in logged)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_github.py tests/test_websearch_connector.py tests/test_sitecheck.py -q` → FAIL

- [ ] **Step 3: Implement**

Her connector'da `nickname = (ctx.target.nickname or "").strip()`, `fullname` boşsa handle
dallarına geç; `generate_variants(..., nickname=ctx.target.nickname)` geçir; `github`
`in:name` sorgusunu yalnız isim varsa üret. `scanner.py`'de connector plan turu sonunda
`all_task_specs` boşsa ve `target.kind == "handle"` ise log:
`"Hedef yalnızca kullanıcı adı içeriyor; hiçbir connector görev üretilemedi."`

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_github.py tests/test_websearch_connector.py tests/test_sitecheck.py tests/test_rendered_identity.py tests/test_publicapi.py tests/test_hackthebox.py -q` → PASS

- [ ] **Step 5: Commit**

```bash
git add packages/connectors tests/test_github.py tests/test_websearch_connector.py tests/test_sitecheck.py
git commit -m "feat(connectors): handle-first task planning"
```

---

### Task 8: Planner handle-farkında (Faz 1 = A)

**Files:**
- Modify: `packages/engine/showis_engine/planner.py:80-160, 601-613, 653-670`
- Modify: `packages/llm/showis_llm/prompts/query_planner.jinja`
- Test: `tests/test_planner.py`

**Interfaces:**
- Produces: `build_template_fallback_queries(target, variants)` handle modunda isim sorgusu
  üretmez; `finalize_query` şablon çıktısına da uygulanır.
- Prompt değişkenleri: `first_name`, `last_name`, `hints`, `variants`, **`kind`**, **`nickname`**.

- [ ] **Step 1: Write the failing test**

```python
def test_handle_mode_plans_nick_queries_only():
    plan = build_template_fallback_queries(handle_target("kerem"),
                                          generate_variants("", "", {}, nickname="kerem"))
    queries = [q.query for q in plan.queries]
    assert queries and all("kerem" in q for q in queries)
    assert not any('""' in q for q in queries)
    assert not any("Kerem" in q for q in queries)

def test_name_mode_plan_is_unchanged():
    plan = build_template_fallback_queries(name_target("Kerem", "Boz"), None)
    assert plan.queries[0].query == '"Kerem Boz" site:github.com'

def test_template_queries_pass_finalize_query():
    plan = build_template_fallback_queries(handle_target("kerem"), None)
    assert all(q.query == finalize_query(q.query) for q in plan.queries)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_planner.py -q` → FAIL

- [ ] **Step 3: Implement**

`fullname = target.display_name` mantığı yerine: `kind/handle` dalı; handle modunda
`fullname` boş kabul edilip nick sorguları üretilir; `nickname` prompt'a eklenir;
şablon çıktısı `finalize_query` ile süzülür (hata → o sorgu atlanır).

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_planner.py tests/test_llm_client.py -q` → PASS

- [ ] **Step 5: Commit**

```bash
git add packages/engine/showis_engine/planner.py packages/llm/showis_llm/prompts/query_planner.jinja tests/test_planner.py
git commit -m "feat(engine): handle-aware query planning"
```

---

### Task 9: Tahmin, explainer ve rapor

**Files:**
- Modify: `packages/engine/showis_engine/scan_modes.py:400`
- Modify: `packages/engine/showis_engine/llm_explainer.py:64`
- Modify: `packages/engine/showis_engine/report.py:321, 522`
- Modify: `apps/api/showis/services/report_service.py:196-201`
- Test: `tests/test_scan_modes.py`, `tests/test_report_quality.py`

**Interfaces:**
- Produces: handle hedefinde `estimate_scan` > 0 adım; rapor/JSON `display_name` kullanır, `nickname`+`kind` taşır.

- [ ] **Step 1: Write the failing tests**

```python
def test_estimate_counts_handle_variants():
    est = estimate_scan(handle_target("kerem"), "O2")
    assert est["tasks"] > 0
    site = next(s for s in est["steps"] if s["key"] == "sitecheck")
    assert site["tasks"] > 0

def test_report_renders_nick_when_name_missing():
    html = render_report(target={"kind": "handle", "nickname": "kerem", "first_name": "", "last_name": ""})
    assert "@kerem" in html and "None" not in html and "Ad Soyad:</b> <br/>" not in html
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_scan_modes.py tests/test_report_quality.py -q` → FAIL

- [ ] **Step 3: Implement**

`generate_variants(..., nickname=target.nickname)`; explainer `target.display_name`;
rapor şablonlarında `display_name`; `report_service` target bloğuna `nickname`,`kind`;
`executive_summary.target_name` alanı da `display_name`'e bağlanır (isim yoksa `@nick`).

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_scan_modes.py tests/test_report_quality.py -q` → PASS

- [ ] **Step 5: Commit**

```bash
git add packages/engine/showis_engine/scan_modes.py packages/engine/showis_engine/llm_explainer.py packages/engine/showis_engine/report.py apps/api/showis/services/report_service.py tests/test_scan_modes.py tests/test_report_quality.py
git commit -m "feat(engine): report and estimate use display_name"
```

---

### Task 10: Web arayüzü — iki modlu form

**Files:**
- Modify: `apps/web/app/cases/new/page.tsx:29-31, 74-95, 120-145`
- Modify: `apps/web/app/cases/[id]/layout.tsx:86-94`
- Modify: `apps/web/lib/api.ts:17, 46-51, 189`
- Verify: `apps/web/lib/types.generated.ts` (Task 11'de üretilir)

**Interfaces:**
- Consumes: `POST /cases` gövdesi `{target: {kind, first_name, last_name, nickname, hints}}`.
- Produces: form durumu `mode: "name" | "handle"`; handle modunda `nickname` zorunlu
  (`@` öneki soyulur), `first_name`/`last_name` opsiyonel.

- [ ] **Step 1: Implement form**

`EMPTY`'ye `mode: "name"`, `nickname: ""`; segment butonları; handle modunda `Ad`/`Soyad`
alanları `(ops.)` etiketiyle ve `required` kaldırılır; submit'te
`target: { kind: mode, first_name, last_name, nickname: nickname.replace(/^@/, ""), hints }`.

- [ ] **Step 2: Update detail chip**

`{target.display_name ?? ...}` yerine: `first_name`/`last_name` varsa `Ad Soyad`,
`nickname` varsa `· @nick` eklenir — spec §6.6.

- [ ] **Step 3: Type check + build**

Run: `cd apps/web && npx tsc --noEmit` → 0 hata
Run: `cd apps/web && npm run build` → başarılı

- [ ] **Step 4: Manual smoke (browse)**

`http://localhost:8083` → Yeni vaka → "Kullanıcı adıyla ara" → `@kerem` → kaydet →
vaka detayında `@kerem` chip'i görünür.

- [ ] **Step 5: Commit**

```bash
git add apps/web/app/cases/new/page.tsx "apps/web/app/cases/[id]/layout.tsx" apps/web/lib/api.ts
git commit -m "feat(web): handle-first case form"
```

---

### Task 11: Şema üretimi + Faz 1 kapısı

**Files:**
- Generated: `apps/web/lib/types.generated.ts`, `packages/contracts/schemas/*.json`

- [ ] **Step 1: Regenerate**

Run: `make schemas`

- [ ] **Step 2: Gate**

Run: `make gate` → tüm adımlar PASS (ruff, mypy, pytest, tsc, build, şema diff, secret)

- [ ] **Step 3: Commit**

```bash
git add apps/web/lib/types.generated.ts packages/contracts/schemas
git commit -m "chore(contracts): regenerate schemas for target identity"
```

---

## FAZ 2 — Planner'ı gerçekten çalıştır

> Faz 1 kapısı yeşil olmadan başlamaz.

### Task 12: `planner_cap(mode)`

**Files:**
- Modify: `packages/engine/showis_engine/scan_modes.py` (MODE_PROFILES yanına)
- Test: `tests/test_scan_modes.py`

**Interfaces:**
- Produces: `planner_cap(mode: str | None) -> int` → O1:0, O2:8, O3:20; bilinmeyen mod `norm_mode` ile O2'ye düşer → 8.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.parametrize("mode,expected", [("O1", 0), ("O2", 8), ("O3", 20), ("o3", 20), ("x", 8)])
def test_planner_cap_by_mode(mode, expected):
    assert planner_cap(mode) == expected
```

- [ ] **Step 2: Run** → `.venv/bin/pytest tests/test_scan_modes.py -q` → FAIL
- [ ] **Step 3: Implement** `PLANNER_CAP = {"O1": 0, "O2": 8, "O3": 20}` + `return PLANNER_CAP[norm_mode(mode)]`
- [ ] **Step 4: Run** → PASS
- [ ] **Step 5: Commit** `feat(engine): per-mode planner query cap`

---

### Task 13: `QueryPlan` → `TaskSpec` dönüşümü

**Files:**
- Modify: `apps/api/showis/services/scanner.py:331-360`
- Test: `tests/test_planner_tasks.py` (yeni)

**Interfaces:**
- Consumes: `planner_cap` (Task 12), `finalize_query` (Task 8).
- Produces: `ScannerService._planner_task_specs(query_plan, target, mode, existing_queries) -> list[TaskSpec]`
  — `connector="websearch"`, `kind="search"`, `params={"query", "limit", "source": "planner"}`,
  `priority` 1..5 kırpılmış.

- [ ] **Step 1: Write the failing test**

```python
def test_planner_specs_respect_mode_cap():
    plan = plan_with([f'"k{i}" site:github.com' for i in range(30)])
    assert len(svc._planner_task_specs(plan, t, "O2", [])) == 8
    assert svc._planner_task_specs(plan, t, "O1", []) == []

def test_planner_specs_dedupe_and_drop_empty():
    plan = plan_with(['"a" site:x.com', '"a" site:x.com', '"" site:y.com'])
    specs = svc._planner_task_specs(plan, t, "O3", [])
    assert [s.params["query"] for s in specs] == ['"a" site:x.com']

def test_planner_specs_skip_existing_connector_queries():
    specs = svc._planner_task_specs(plan_with(['"a" site:x.com']), t, "O3", ['"a" site:x.com'])
    assert specs == []

def test_planner_specs_are_marked_and_sanitized():
    specs = svc._planner_task_specs(plan_with(['"a<b>" site:x.com']), t, "O2", [])
    assert specs[0].params["source"] == "planner"
    assert specs[0].params["query"] == finalize_query(specs[0].params["query"])
```

- [ ] **Step 2: Run** → `.venv/bin/pytest tests/test_planner_tasks.py -q` → FAIL
- [ ] **Step 3: Implement** (dönüşüm + tekilleştirme + `finalize_query` hatasını yut ve sorguyu atla)
- [ ] **Step 4: Run** → PASS
- [ ] **Step 5: Commit** `feat(api): materialize planner queries as search tasks`

---

### Task 14: Görev listesine bağlama + doğru log

**Files:**
- Modify: `apps/api/showis/services/scanner.py:331-360`
- Test: `tests/integration/test_scan_lifecycle.py`

**Interfaces:**
- Consumes: Task 13'ün `_planner_task_specs`.
- Produces: connector plan turundan sonra `all_task_specs.extend(planner_specs)`; log mesajı
  gerçek eklenen sayıyı yazar; DB'de `tasks.params->>'source' = 'planner'` satırları görünür.

- [ ] **Step 1: Write the failing test**

```python
async def test_o2_scan_creates_planner_tasks(client, case_id, db):
    await client.post(f"/api/v1/cases/{case_id}/scan", json={"mode": "O2"})
    rows = await db.fetch_all("select params from tasks where scan_id = :s", {"s": scan_id})
    planner = [r for r in rows if json.loads(r["params"]).get("source") == "planner"]
    assert 0 < len(planner) <= 8

async def test_o1_scan_has_no_planner_tasks(client, case_id, db):
    ...
    assert planner == []

async def test_planner_tasks_do_not_duplicate_entities(client, case_id, db):
    """Review Focus #5: aynı sonuç iki kez entity yazmamalı."""
    await run_scan(client, case_id, mode="O2")
    rows = await db.fetch_all(
        "select url, count(*) c from entities where case_id = :c group by url having c > 1",
        {"c": case_id},
    )
    assert rows == []
```

- [ ] **Step 2: Run** → FAIL (planner görevi yok)
- [ ] **Step 3: Implement** bağlama + log metni
- [ ] **Step 4: Run** → PASS
- [ ] **Step 5: Commit** `feat(api): execute planner queries as real search tasks`

---

### Task 15: Faz 2 kapısı ve canlı doğrulama

- [ ] **Step 1:** `make gate` → PASS
- [ ] **Step 2:** `bash scripts/final_gate.sh --live` → PASS (stack ayakta olmalı)
- [ ] **Step 3: Elle doğrulama (onaylı):** handle modunda vaka aç, O2 taraması başlat,
  `docker exec showis-postgres psql ... -c "select connector, params->>'source', params->>'query' from tasks where scan_id='...'"`
  → `≤8` planner satırı, `""` yok.
- [ ] **Step 4: Commit** varsa doküman/log düzeltmeleri.

---

### Task 16: Migration'ı canlı stack'e uygula (ONAY GEREKLİ)

- [ ] **Step 1:** Kullanıcıdan onay al.
- [ ] **Step 2:** Yedek: `docker exec showis-postgres pg_dump -U showis showis > /tmp/showis-pre0007.sql`
- [ ] **Step 3:** `docker compose -f infra/docker-compose.yml run --rm api alembic upgrade head`
- [ ] **Step 4:** Doğrula: `\d targets` → `nickname`, `kind` kolonları; `kind` default `'name'`.
- [ ] **Step 5:** API'yi yeniden başlat (`docker compose up -d --no-deps api worker beat`) ve
  `curl :8083/health` → 200.
