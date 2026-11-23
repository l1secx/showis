"use client";

import { useParams } from "next/navigation";
import { DiffTab } from "../panels";

export default function DiffPage() {
  const { id } = useParams<{ id: string }>();
  return <DiffTab caseId={id} />;
}
