import { Suspense } from "react";
import { CasesDashboard } from "./components/cases-dashboard";

export default function DashboardPage() {
  return (
    <Suspense fallback={<p className="text-[13px] text-ink-3">Yükleniyor</p>}>
      <CasesDashboard />
    </Suspense>
  );
}
