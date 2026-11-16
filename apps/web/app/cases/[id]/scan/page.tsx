"use client";

import { Suspense } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { ScanTab } from "../panels";

function ScanPageInner() {
  const { id } = useParams<{ id: string }>();
  const params = useSearchParams();
  const mode = params.get("mode") || undefined;
  return <ScanTab caseId={id} autoStart={params.get("start") === "1"} initialMode={mode} />;
}

export default function ScanPage() {
  return (
    <Suspense fallback={<p className="text-sm text-slate-400">Yükleniyor…</p>}>
      <ScanPageInner />
    </Suspense>
  );
}
