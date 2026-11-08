import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { connection } from "next/server";
import { Suspense } from "react";

import { ApiUnreachableNotice } from "@/components/api-error";
import { api, ApiUnreachable, type MessageOut, type TraceEvent } from "@/lib/api";
import { formatDateTime } from "@/lib/format";

export const metadata: Metadata = { title: "Trace" };

export default function TracePage({ params }: PageProps<"/traces/[id]">) {
  return (
    <div className="flex flex-col gap-6">
      <Link href="/messages" className="text-sm font-medium text-action hover:underline">
        Về nhật ký message
      </Link>
      <Suspense fallback={<div aria-hidden className="h-96 rounded-lg bg-surface" />}>
        <Trace params={params} />
      </Suspense>
    </div>
  );
}

type Span = { label: string; detail: string; start: number; end: number; failed: boolean; attempts: number };

/** One bar per request: when it started and how long it took. Replies are covered by their request's bar. */
function spans(messages: MessageOut[]): Span[] {
  return messages
    .filter((m) => m.type === "request" && m.latency_ms !== null)
    .map((m) => {
      const end = new Date(m.updated_at).getTime();
      const outbound = m.direction === "outbound";
      return {
        label: outbound ? `→ ${m.to_agent}` : `← ${m.from_agent}`,
        detail: `${m.capability ?? ""}${m.attempts > 1 ? `, ${m.attempts} lần thử` : ""}`,
        start: end - (m.latency_ms ?? 0),
        end,
        failed: m.status === "failed",
        attempts: m.attempts,
      };
    })
    .sort((a, b) => a.start - b.start);
}

const NOTABLE = new Set([
  "broker.retry", "client.failover", "registry.fallback_cache", "memory.conflict", "memory.stale",
  "llm.fallback", "msg.rejected", "msg.duplicate",
]);

async function Trace({ params }: { params: Promise<{ id: string }> }) {
  await connection();
  const { id } = await params;
  let events: TraceEvent[];
  let messages: MessageOut[];
  try {
    [events, messages] = await Promise.all([
      api.trace(id),
      api.messages({ trace_id: id, page_size: 200 }).then((p) => p.items),
    ]);
  } catch (error) {
    if (error instanceof ApiUnreachable) return <ApiUnreachableNotice />;
    throw error;
  }
  if (events.length === 0 && messages.length === 0) notFound();

  const bars = spans(messages);
  const times = [
    ...events.map((e) => new Date(e.ts).getTime()),
    ...bars.flatMap((b) => [b.start, b.end]),
  ];
  const t0 = Math.min(...times);
  const total = Math.max(1, Math.max(...times) - t0);
  const pct = (t: number) => ((t - t0) / total) * 100;
  const agents = new Set(messages.flatMap((m) => [m.from_agent, m.to_agent]));

  return (
    <>
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Trace trong {total.toLocaleString("vi-VN")} ms</h1>
        <p className="mt-1 text-sm text-muted">
          <code>{id}</code>, bắt đầu {formatDateTime(new Date(t0).toISOString())}, {Math.max(agents.size, 1)} agent tham
          gia
        </p>
      </div>

      {bars.length > 0 && (
        <section aria-labelledby="waterfall-title" className="rounded-lg border border-line bg-surface p-5">
          <h2 id="waterfall-title" className="font-bold">
            Message giữa các agent
          </h2>
          <ol className="mt-4 flex flex-col gap-2">
            {bars.map((b, i) => (
              <li key={i} className="grid items-center gap-x-4 sm:grid-cols-[13rem_1fr]">
                <span className="truncate text-sm">
                  <span className="font-medium">{b.label}</span> <span className="text-muted">{b.detail}</span>
                </span>
                <span className="group relative h-6 rounded-[4px] bg-paper">
                  <span
                    className={`absolute inset-y-1 min-w-1 rounded-[4px] ${b.failed ? "bg-warn" : "bg-action"}`}
                    style={{ left: `${pct(b.start)}%`, width: `${pct(b.end) - pct(b.start)}%` }}
                  />
                  <span className="pointer-events-none absolute -top-1 z-10 hidden -translate-y-full rounded-md border border-line bg-surface px-2 py-1 text-xs whitespace-nowrap shadow-sm group-hover:block"
                    style={{ left: `${Math.min(pct(b.start), 70)}%` }}>
                    bắt đầu +{b.start - t0} ms, kéo dài {b.end - b.start} ms{b.failed ? ", thất bại" : ""}
                  </span>
                </span>
              </li>
            ))}
          </ol>
          <div className="mt-2 flex justify-between text-xs text-muted sm:ml-[14rem]">
            <span>0 ms</span>
            <span>{total.toLocaleString("vi-VN")} ms</span>
          </div>
        </section>
      )}

      <section aria-labelledby="events-title">
        <h2 id="events-title" className="font-bold">
          {events.length} sự kiện
        </h2>
        <ol className="mt-3 divide-y divide-line border-y border-line text-sm">
          {events.map((e, i) => {
            const at = new Date(e.ts).getTime() - t0;
            const notable = NOTABLE.has(e.event);
            return (
              <li key={i} className="grid gap-x-4 py-2 sm:grid-cols-[5rem_12rem_1fr]">
                <span className="text-muted tabular-nums">+{at} ms</span>
                <span className={notable ? "font-medium text-warn" : "font-medium"}>{e.event}</span>
                <span className="break-words text-xs text-muted">
                  {Object.entries(e.attrs)
                    .filter(([, v]) => v !== null && v !== "")
                    .map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
                    .join("; ")}
                </span>
              </li>
            );
          })}
        </ol>
      </section>
    </>
  );
}
