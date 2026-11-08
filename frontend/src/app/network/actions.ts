"use server";

import { AdminUnauthorized, adminJson } from "@/lib/admin";
import { ApiUnreachable, type CallResponse } from "@/lib/api";

export type TestCallState = { result: CallResponse | null; error: string | null; loginRequired?: boolean };

export async function sendTestMessage(_previous: TestCallState, formData: FormData): Promise<TestCallState> {
  let payload: unknown;
  try {
    payload = JSON.parse(String(formData.get("payload") || "{}"));
  } catch {
    return { result: null, error: "Payload phải là JSON hợp lệ, ví dụ {\"symbol\": \"TCB\"}." };
  }
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
    return { result: null, error: "Payload phải là một JSON object." };
  }
  try {
    const result = await adminJson<CallResponse>("/api/v1/admin/agents/call", {
      method: "POST",
      body: JSON.stringify({
        agent_id: String(formData.get("agent_id")),
        capability: String(formData.get("capability")),
        payload,
      }),
    });
    return { result, error: null };
  } catch (error) {
    if (error instanceof AdminUnauthorized) return { result: null, error: null, loginRequired: true };
    if (error instanceof ApiUnreachable) return { result: null, error: "Không kết nối được agent-api." };
    return { result: null, error: error instanceof Error ? error.message : "Gửi thất bại." };
  }
}
