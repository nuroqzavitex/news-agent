"use client";

import { useActionState } from "react";

import type { FaultConfig, MockAgentState } from "@/lib/api";

import { type FaultFormState, updateAgentFaults, updateMockFault } from "./actions";

const initial: FaultFormState = { message: null, error: null };
const input = "rounded-md border border-line bg-surface px-2 py-1.5 text-sm";

const SWITCHES: { name: keyof FaultConfig; label: string; effect: string }[] = [
  {
    name: "llm_down",
    label: "Gemini không phản hồi",
    effect: "Tóm tắt chuyển sang trích câu đầu, sentiment dùng từ điển, /health báo Gemini lỗi.",
  },
  {
    name: "registry_down",
    label: "Registry không phản hồi",
    effect: "Agent tìm agent khác trong bộ nhớ đệm; trace có sự kiện registry.fallback_cache.",
  },
  {
    name: "identity_down",
    label: "Identity không phản hồi",
    effect: "Token đã kiểm tra trong 60 giây qua vẫn được nhận, token lạ bị từ chối.",
  },
  {
    name: "tracer_down",
    label: "Observability không phản hồi",
    effect: "Trace vẫn được ghi vào cơ sở dữ liệu của agent; /health báo Observability lỗi.",
  },
];

function Feedback({ state }: { state: FaultFormState }) {
  return (
    <p role="status" className={`text-sm ${state.error ? "text-warn" : "text-ok"}`}>
      {state.error ?? state.message}
    </p>
  );
}

export function AgentFaultsForm({ config, enabled }: { config: FaultConfig; enabled: boolean }) {
  const [state, action, pending] = useActionState(updateAgentFaults, initial);
  return (
    <form action={action} className="flex flex-col gap-4">
      <fieldset disabled={!enabled || pending} className="flex flex-col gap-3">
        <legend className="sr-only">Lỗi giả lập của agent này</legend>
        {SWITCHES.map((s) => (
          <label key={s.name} className="flex gap-3">
            <input type="checkbox" name={s.name} defaultChecked={Boolean(config[s.name])} className="mt-1 size-4" />
            <span>
              <span className="block text-sm font-medium">{s.label}</span>
              <span className="block text-xs text-muted">{s.effect}</span>
            </span>
          </label>
        ))}
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="text-sm font-medium">
            Agent này trả lời chậm (giây)
            <input
              name="inbound_delay_s"
              type="number"
              min={0}
              max={60}
              step={0.5}
              defaultValue={config.inbound_delay_s ?? 0}
              className={`mt-1 block w-full ${input}`}
            />
            <span className="mt-1 block text-xs font-normal text-muted">Agent khác gọi tới sẽ bị timeout và phải retry.</span>
          </label>
          <label className="text-sm font-medium">
            Agent này trả lỗi
            <select
              name="inbound_fail_status"
              defaultValue={config.inbound_fail_status ?? ""}
              className={`mt-1 block w-full ${input}`}
            >
              <option value="">Không</option>
              <option value="500">HTTP 500</option>
              <option value="503">HTTP 503</option>
            </select>
            <span className="mt-1 block text-xs font-normal text-muted">Agent khác gọi tới sẽ chuyển sang agent dự phòng.</span>
          </label>
        </div>
      </fieldset>
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="submit"
          disabled={!enabled || pending}
          className="rounded-md bg-action px-4 py-2 text-sm font-medium text-white hover:bg-action-strong disabled:opacity-60"
        >
          Áp dụng
        </button>
        <button
          type="submit"
          name="intent"
          value="reset"
          disabled={!enabled || pending}
          className="rounded-md border border-line px-4 py-2 text-sm font-medium hover:border-ink disabled:opacity-60"
        >
          Tắt tất cả
        </button>
        <Feedback state={state} />
      </div>
    </form>
  );
}

export function MockFaultForm({ mock, enabled }: { mock: MockAgentState; enabled: boolean }) {
  const [state, action, pending] = useActionState(updateMockFault, initial);
  const fault = mock.fault;
  const active = fault && (fault.delay_s || fault.fail_status);
  return (
    <form action={action} className="grid gap-x-6 gap-y-2 py-4 sm:grid-cols-[14rem_1fr]">
      <div>
        <p className="font-medium">{mock.agent_id}</p>
        <p className={`text-xs ${!mock.reachable || active ? "font-medium text-warn" : "text-muted"}`}>
          {!mock.reachable
            ? "Không kết nối được (container đang tắt?)"
            : active
              ? `Đang lỗi: ${fault?.delay_s ? `chậm ${fault.delay_s} giây` : ""}${fault?.delay_s && fault?.fail_status ? ", " : ""}${fault?.fail_status ? `trả HTTP ${fault.fail_status}` : ""}`
              : `Bình thường, ${mock.capabilities.join(", ")}`}
        </p>
      </div>
      <fieldset disabled={!enabled || !mock.reachable || pending} className="flex flex-wrap items-end gap-3">
        <legend className="sr-only">Lỗi giả lập của {mock.agent_id}</legend>
        <input type="hidden" name="agent_id" value={mock.agent_id} />
        <label className="text-xs text-muted">
          Chậm (giây)
          <input name="delay_s" type="number" min={0} max={60} step={0.5} defaultValue={fault?.delay_s ?? 0} className={`mt-1 block w-24 ${input}`} />
        </label>
        <label className="text-xs text-muted">
          Trả lỗi
          <select name="fail_status" defaultValue={fault?.fail_status ?? ""} className={`mt-1 block ${input}`}>
            <option value="">Không</option>
            <option value="500">HTTP 500</option>
            <option value="503">HTTP 503</option>
          </select>
        </label>
        <label className="text-xs text-muted">
          Số lần lỗi
          <input name="fail_times" type="number" min={1} max={100} placeholder="mãi mãi" defaultValue={fault?.fail_times ?? ""} className={`mt-1 block w-24 ${input}`} />
        </label>
        <button type="submit" className="rounded-md border border-line px-3 py-1.5 text-sm font-medium hover:border-ink disabled:opacity-60">
          Áp dụng
        </button>
        <button type="submit" name="intent" value="reset" className="rounded-md px-3 py-1.5 text-sm font-medium text-action hover:underline disabled:opacity-60">
          Về bình thường
        </button>
      </fieldset>
      <div className="sm:col-start-2">
        <Feedback state={state} />
      </div>
    </form>
  );
}
