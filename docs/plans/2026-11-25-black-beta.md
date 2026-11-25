# Showis Beta — saf siyah arayüz + özel grafik motoru

Kapsam (PROMPT.md): (1) havuz yalnız kullanıcı onayıyla güncellenir, (2) havuz üzerinde
araştırma + tedarik zinciri, (3) sadeleşme, tamamen siyah tema (gri yüzey yok),
(4) özel grafik motoru, (5) çalışan beta.

Orkestrasyon: işi DeepSeek v4.1 flash işçileri (`opencode2 run -m opencode-go/deepseek-v4.1-flash`)
yapar; her dalga sonunda `make gate` ve hedefli testlerle doğrulanır.

## Dalga 1 (paralel, ayrık dosyalar)
- W1 `apps/web/lib/graph-engine/**`: bağımsız grafik motoru (Barnes-Hut kuvvet simülasyonu,
  Canvas2D çizici, kamera, hit-test, LOD, dışa aktarım). Testler `node --test`.
- W2 tema: `globals.css`, `tailwind.config.js`, `DESIGN.md` — tek saf siyah tema.
- W3 backend denetimi: havuz onay invariant'ı, araştırma+tedarik zinciri boşlukları (testle).

## Dalga 2
- W4 `graph.tsx` cytoscape → graph-engine entegrasyonu, cytoscape bağımlılığı kalkar.
- W5 sade UI: panels/nav/dashboard karmaşıklık azaltma, siyah tema cilası.

## Dalga 3
- Playwright ile canlı görsel doğrulama, gate, docker rebuild, README/HANDOFF güncelleme.
