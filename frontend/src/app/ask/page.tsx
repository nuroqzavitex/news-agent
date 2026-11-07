import type { Metadata } from "next";

import { AskForm } from "./ask-form";

export const metadata: Metadata = { title: "Hỏi agent" };

export default function AskPage() {
  return <AskForm />;
}
