"use client";

import { useParams } from "next/navigation";
import { CommentsTab } from "../panels";

export default function CommentsPage() {
  const { id } = useParams<{ id: string }>();
  return <CommentsTab caseId={id} />;
}
