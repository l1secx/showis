"use client";

/**
 * Kimlik zinciri paneli: onaylı bulgulardan (hedef, seed'ler, `confirmed`
 * kayıtlar) sahibin kendi yazdığı bağlantıları izleyerek diğer hesapları
 * bulur. Sonuçlar havuza `proposed` öneri olarak girer; onay yine analistindir.
 */

import { useState } from "react";
import { GitBranch, Loader2 } from "lucide-react";
import { api, type ChainNode, type ChainSummary } from "../../../../lib/api";

const RELATION_TR: Record<string, string> = {
  proof: "doğrulanmış kanıt",
  owner: "sahip alanı",
  rel_me: "rel=me",
  readme: "profil README",
  aggregator: "bağlantı sayfası",
  commit: "commit e-postası",
  bio: "biyografi",
  site: "kişisel site",
  backlink: "karşılıklı bağlantı",
  name: "isim eşleşmesi",
  handle_probe: "aynı kullanıcı adı",
  search: "arama sonucu",
};

function trustTone(trust: number): string {
  if (trust >= 0.9) return "text-ok";
  if (trust >= 0.7) return "text-ink";
  return "text-warn";
}

function nodeTitle(node: ChainNode): string {
  if (node.kind === "profile") return `${node.network} · ${node.value}`;
  return node.value;
}

export function ChainPanel({ caseId, onDone }: { caseId: string; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ChainSummary | null>(null);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      const summary = await api.runChain(caseId);
      setResult(summary);
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Kimlik zinciri kurulamadı");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel" aria-labelledby="chain-title">
      <div className="panel-head flex-wrap">
        <div className="min-w-0">
          <h2 id="chain-title" className="panel-title">
            Kimlik zinciri
          </h2>
          <p className="mt-0.5 max-w-[68ch] text-[12px] text-ink-3">
            Onayladığınız hesaplardan başlar; GitHub/GitLab profil alanları, Keybase ve
            Gravatar kanıtları, linktree ve kişisel sitelerdeki rel=me bağlantılarını izler.
            Karşılıklı bağlantı veya isim eşleşmesi olmayan tahminler elenir.
          </p>
        </div>
        <button type="button" className="btn-secondary btn-sm" onClick={run} disabled={busy}>
          {busy ? (
            <Loader2 size={12} className="animate-spin" aria-hidden />
          ) : (
            <GitBranch size={12} aria-hidden />
          )}
          {busy ? "Zincir kuruluyor…" : "Zinciri kur"}
        </button>
      </div>
      {error && (
        <p role="alert" className="border-t border-line px-5 py-3 text-[12.5px] text-danger">
          {error}
        </p>
      )}
      {result && (
        <div className="border-t border-line px-5 py-3 text-[12.5px]" role="status">
          <p className="text-ink-2">
            {result.anchors} çapa · {result.discovered} hesap/sayfa bulundu (
            {result.high_confidence} yüksek güven) · {result.created} yeni öneri ·{" "}
            {result.relations} ilişki · {result.fetches} istek
          </p>
          {result.nodes.length === 0 ? (
            <p className="mt-2 text-ink-3">
              Zincir yeni hesap bulamadı. Önce en az bir hesabı (ör. GitHub profil adresi)
              havuzda onaylayın.
            </p>
          ) : (
            <ul className="mt-2 divide-y divide-line">
              {result.nodes.slice(0, 30).map((node) => (
                <li key={node.key} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-1.5">
                  <span className={`w-12 shrink-0 tabular-nums ${trustTone(node.trust)}`}>
                    %{Math.round(node.trust * 100)}
                  </span>
                  {node.url ? (
                    <a
                      href={node.url}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="min-w-0 break-all font-medium text-ink hover:underline"
                    >
                      {nodeTitle(node)}
                    </a>
                  ) : (
                    <span className="min-w-0 break-all font-medium text-ink">{nodeTitle(node)}</span>
                  )}
                  {node.display_name && <span className="text-ink-3">{node.display_name}</span>}
                  <span className="basis-full pl-[60px] text-[11.5px] text-ink-3">
                    {node.evidence
                      .map((ev) => `${RELATION_TR[ev.relation] ?? ev.relation} ← ${ev.from}`)
                      .join(" · ")}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
