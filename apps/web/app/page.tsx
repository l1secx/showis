import { Suspense } from "react";
import { CasesDashboard } from "./components/cases-dashboard";

export default function DashboardPage() {
  return (
    <Suspense fallback={<p className="text-slate-400">Yükleniyor…</p>}>
      <CasesDashboard />
    </Suspense>
  );
}
