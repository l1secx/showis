"use client";

import { useParams } from "next/navigation";
import { FindingsTab } from "../panels";

export default function FindingsPage() {
  const { id } = useParams<{ id: string }>();
  return <FindingsTab caseId={id} />;
}
