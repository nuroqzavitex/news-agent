"use server";

import { api, ApiUnreachable, type AskResult } from "@/lib/api";

export type AskState = { result: AskResult | null; error: string | null };

export async function askAgent(_previous: AskState, formData: FormData): Promise<AskState> {
  // An example button submits its own "question" after the textarea's, so the last value is the one meant.
  const question = String(formData.getAll("question").at(-1) ?? "").trim();
  if (!question) return { result: null, error: "Nhập câu hỏi trước khi gửi." };
  try {
    return { result: await api.ask(question.slice(0, 1000)), error: null };
  } catch (error) {
    if (error instanceof ApiUnreachable) {
      return { result: null, error: "Không kết nối được agent-api. Chạy docker compose up -d rồi thử lại." };
    }
    return { result: null, error: "Agent không trả lời được câu hỏi này. Xem log của agent-api để biết lý do." };
  }
}
