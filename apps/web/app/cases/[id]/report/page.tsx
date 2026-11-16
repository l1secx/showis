"use client";

import { useParams } from "next/navigation";
import { ReportTab } from "../panels";

export default function ReportPage() {
  const { id } = useParams<{ id: string }>();
  return <ReportTab caseId={id} />;
}
