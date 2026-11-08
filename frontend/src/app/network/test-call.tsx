"use client";

import Link from "next/link";
import { useActionState } from "react";

import { sendTestMessage, type TestCallState } from "./actions";

const EXAMPLE_PAYLOAD: Record<string, string> = {
  stock_price: '{"symbol": "TCB"}',
  weather: '{"location": "Hà Nội"}',
};

export function TestCall({ agentId, capabilities }: { agentId: string; capabilities: string[] }) {
  const [state, action, pending] = useActionState<TestCallState, FormData>(sendTestMessage, {
    result: null,
    error: null,
  });
  const first = capabilities[0] ?? "";
  return (
    <details className="mt-2">
      <summary className="cursor-pointer text-sm font-medium text-action">Gửi message thử</summary>
      <form action={action} className="mt-3 flex flex-col gap-2">
        <input type="hidden" name="agent_id" value={agentId} />
        <label className="text-xs text-muted">
          Capability
          <select name="capability" defaultValue={first} className="mt-1 block w-full rounded-md border border-line bg-surface px-2 py-1.5 text-sm text-ink">
            {capabilities.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </label>
        <label className="text-xs text-muted">
          Payload (JSON)
          <textarea
            name="payload"
            rows={2}
            defaultValue={EXAMPLE_PAYLOAD[first] ?? "{}"}
            className="mt-1 block w-full rounded-md border border-line bg-surface px-2 py-1.5 font-mono text-xs text-ink"
          />
        </label>
        <button
          type="submit"
          disabled={pending}
          className="self-start rounded-md border border-line px-3 py-1.5 text-sm font-medium hover:border-ink disabled:opacity-60"
        >
          {pending ? "Đang gửi" : `Gửi tới ${agentId}`}
        </button>
        <div role="status" className="text-xs">
          {state.loginRequired && (
            <p>
              Cần <Link href="/login?next=/network" className="font-medium text-action hover:underline">đăng nhập</Link>{" "}
              để gửi message thay mặt agent này.
            </p>
          )}
          {state.error && <p className="text-warn">{state.error}</p>}
          {state.result && (
            <div className="flex flex-col gap-1">
              <p className={state.result.ok ? "font-medium text-ok" : "font-medium text-warn"}>
                {state.result.ok
                  ? `Trả lời sau ${state.result.latency_ms} ms, ${state.result.attempts} lần thử`
                  : `Thất bại sau ${state.result.attempts} lần thử: ${state.result.error}`}
              </p>
              {state.result.payload && (
                <pre className="max-h-40 overflow-auto rounded-md bg-paper p-2">
                  {JSON.stringify(state.result.payload, null, 2)}
                </pre>
              )}
              <Link href={`/traces/${state.result.trace_id}`} className="text-action hover:underline">
                Xem trace
              </Link>
            </div>
          )}
        </div>
      </form>
    </details>
  );
}
