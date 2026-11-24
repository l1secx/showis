"use client";

import { useParams } from "next/navigation";
import { FindingsTab } from "../panels";
import { ScanSessionProvider } from "../scan-context";

export default function FindingsPage() {
  const { id } = useParams<{ id: string }>();
  return (
    <ScanSessionProvider caseId={id}>
      <FindingsTab caseId={id} />
    </ScanSessionProvider>
  );
}
