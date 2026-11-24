"use client";

import { Suspense } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { ScanTab } from "../panels";
import { ScanSessionProvider } from "../scan-context";

function ScanPageInner() {
  const { id } = useParams<{ id: string }>();
  const params = useSearchParams();
  const mode = params.get("mode") || undefined;
  return (
    <ScanSessionProvider caseId={id}>
      <ScanTab caseId={id} autoStart={params.get("start") === "1"} initialMode={mode} />
    </ScanSessionProvider>
  );
}

export default function ScanPage() {
  return (
    <Suspense fallback={<p className="text-[13px] text-ink-3">Yükleniyor</p>}>
      <ScanPageInner />
    </Suspense>
  );
}
