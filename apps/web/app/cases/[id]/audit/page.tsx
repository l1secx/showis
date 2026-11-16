"use client";

import { useParams } from "next/navigation";
import { AuditTab } from "../panels";

export default function AuditPage() {
  const { id } = useParams<{ id: string }>();
  return <AuditTab caseId={id} />;
}
