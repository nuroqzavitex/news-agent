"use server";

import { refresh } from "next/cache";
import { redirect } from "next/navigation";

import { AdminUnauthorized, adminJson } from "@/lib/admin";

export type FaultFormState = { message: string | null; error: string | null };

function number(formData: FormData, name: string): number | null {
  const raw = String(formData.get(name) ?? "").trim();
  if (!raw) return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

async function save(path: string, body: unknown, done: string): Promise<FaultFormState> {
  try {
    await adminJson(path, { method: "PUT", body: JSON.stringify(body) });
  } catch (error) {
    if (error instanceof AdminUnauthorized) redirect("/login?next=/faults");
    return { message: null, error: error instanceof Error ? error.message : "Không lưu được." };
  }
  refresh();
  return { message: done, error: null };
}

export async function updateAgentFaults(_previous: FaultFormState, formData: FormData): Promise<FaultFormState> {
  if (formData.get("intent") === "reset") return save("/api/v1/admin/faults", {}, "Đã tắt mọi lỗi giả lập.");
  const failStatus = number(formData, "inbound_fail_status");
  return save(
    "/api/v1/admin/faults",
    {
      inbound_delay_s: number(formData, "inbound_delay_s") ?? 0,
      inbound_fail_status: failStatus || null,
      llm_down: formData.get("llm_down") === "on",
      registry_down: formData.get("registry_down") === "on",
      identity_down: formData.get("identity_down") === "on",
      tracer_down: formData.get("tracer_down") === "on",
    },
    "Đã áp dụng. Gửi một câu hỏi ở trang Hỏi agent để xem agent xử lý thế nào.",
  );
}

export async function updateMockFault(_previous: FaultFormState, formData: FormData): Promise<FaultFormState> {
  const agentId = String(formData.get("agent_id"));
  const body =
    formData.get("intent") === "reset"
      ? {}
      : {
          delay_s: number(formData, "delay_s") ?? 0,
          fail_status: number(formData, "fail_status") || null,
          fail_times: number(formData, "fail_times") || null,
        };
  return save(`/api/v1/admin/mocks/${encodeURIComponent(agentId)}/fault`, body, `Đã cập nhật ${agentId}.`);
}
