"use client";

import dynamic from "next/dynamic";
import { useParams } from "next/navigation";

const CaseGraph = dynamic(() => import("../graph"), { ssr: false });

export default function GraphPage() {
  const { id } = useParams<{ id: string }>();
  return <CaseGraph caseId={id} />;
}
