# Şema ve TypeScript tip üretimi (T-334)

Tek yönlü hat — tek kaynak Pydantic modelleridir:

```
packages/contracts/showis_contracts/{enums,findings,entities,...}.py
        │  make schemas   (packages/contracts/scripts/export_schemas.py)
        ├──▶ packages/contracts/schemas/*.json        (JSON Schema)
        └──▶ apps/web/lib/types.generated.ts          (TS tipleri — OTOMATİK)
```

## Kurallar

1. **`apps/web/lib/types.generated.ts` elle düzenlenmez.** `make schemas` her çalıştığında
   baştan yazılır; başlığında "OTOMATİK ÜRETİLDİ" uyarısı vardır.
2. **`apps/web/lib/types.ts` el yazımı tipleri barındırır** (rapor bölümleri, UI'ya özel
   yardımcı tipler) ve üretilen dosyayı `export * from "./types.generated";` ile dışa aktarır.
   Jeneratör bu dosyaya **dokunmaz**. Aynı isim hem burada hem üretilen dosyada tanımlıysa
   **yerel tanım kazanır** (ES modül semantiği).
3. Yeni bir sözleşme tipi/alanı gerekiyorsa kaynağı `showis_contracts`'tur; `types.ts`'e
   elle tip eklenmez.
4. Enum'lar (`Platform`, `FindingStatus`, `EntityType`, `EdgeType`, `ObservationKind`,
   `RiskTier`) doğrudan `enums.py`'den TS union'a çevrilir — yeni bir enum değeri otomatik
   görünür (T-308 `stackoverflow` örneği).

## Tip eşlemesi

| Python / JSON Schema | TypeScript |
| --- | --- |
| `str` / `{"type":"string"}` | `string` |
| `datetime`, `date` | `string` (ISO) |
| `int`, `float` | `number` |
| `bool` | `boolean` |
| `T \| None` (`anyOf: [T, null]`) | `T \| null` |
| `list[T]` | `T[]` |
| `dict[str, Any]` | `Record<string, unknown>` |
| `$ref` (`#/$defs/X`) | `X` |
| varsayılanı olan alan (`required` dışı) | `alan?: T` |
| varsayılanı olmayan alan | `alan: T` |

Not: opsiyonellik JSON Schema'nın `required` listesine göredir; Pydantic'te varsayılanı olan
alanlar opsiyonel çıkar (sunucu yanıtlarında genelde dolu olsalar da).

`Case` bir sözleşme modeli değildir (DB modeli `apps/api`'de); UI'ya özel `CaseSummary` /
`CaseDetail` / `FindingItem` gibi tipler `apps/web/lib/api.ts` içinde kalır.

## Doğrulama

```bash
make schemas                     # JSON + types.generated.ts
cd apps/web && npx tsc --noEmit
cd apps/web && npm run build
PYTHONPATH=packages/contracts .venv/bin/pytest packages/contracts/tests -q
```

Regresyon testleri (`packages/contracts/tests/test_export_schemas.py`):
- her enum değeri union'da var mı (yeni Platform değerleri dahil),
- `Finding.authenticity` / `authenticity_reasons` üretiliyor mu,
- tip eşlemesi (`datetime→string`, `dict→Record`, `list[Model]→Model[]`),
- idempotentlik (iki üretim aynı çıktı),
- jeneratör yalnız `types.generated.ts`'i hedefliyor (el yazımı `types.ts`'e dokunmuyor).
