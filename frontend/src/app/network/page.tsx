import type { Metadata } from "next";
import { connection } from "next/server";
import { Suspense } from "react";

import { ApiUnreachableNotice } from "@/components/api-error";
import { api, ApiUnreachable, type Network } from "@/lib/api";
import { formatAgo } from "@/lib/format";

import { TestCall } from "./test-call";

export const metadata: Metadata = { title: "Mạng agent" };

const WINDOW_MINUTES = 60;

export default function NetworkPage() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-bold tracking-tight">Mạng agent</h1>
      <Suspense fallback={<div aria-hidden className="h-[480px] rounded-lg border border-line bg-surface" />}>
        <NetworkView />
      </Suspense>
    </div>
  );
}

type Node = { id: string; label: string; sub: string; x: number; y: number; self: boolean; known: boolean };
type Edge = { peer: string; out: number; in: number; failed: number; latency: number | null };

const W = 720;
const H = 440;

/** This agent in the middle, the others on a circle around it. */
function layout(data: Network): { nodes: Node[]; edges: Edge[] } {
  const self = data.nodes.find((n) => n.is_self)!;
  const known = data.nodes.filter((n) => !n.is_self);
  const strangers = [...new Set(data.edges.map((e) => e.peer))].filter((p) => !known.some((n) => n.agent_id === p));
  const others = [
    ...known.map((n) => ({ id: n.agent_id, sub: n.capabilities.join(", "), known: true })),
    ...strangers.map((id) => ({ id, sub: "chưa đăng ký", known: false })),
  ];
  const r = Math.min(W, H) / 2 - 70;
  const nodes: Node[] = [
    { id: self.agent_id, label: self.agent_id, sub: "agent này", x: W / 2, y: H / 2, self: true, known: true },
    ...others.map((o, i) => {
      const angle = (2 * Math.PI * i) / Math.max(others.length, 1) - Math.PI / 2;
      return { ...o, label: o.id, x: W / 2 + r * 1.35 * Math.cos(angle), y: H / 2 + r * Math.sin(angle), self: false };
    }),
  ];
  const byPeer = new Map<string, Edge>();
  for (const e of data.edges) {
    const edge = byPeer.get(e.peer) ?? { peer: e.peer, out: 0, in: 0, failed: 0, latency: null };
    if (e.direction === "outbound") edge.out += e.messages;
    else edge.in += e.messages;
    edge.failed += e.failed;
    edge.latency = e.avg_latency_ms ?? edge.latency;
    byPeer.set(e.peer, edge);
  }
  return { nodes, edges: [...byPeer.values()] };
}

async function NetworkView() {
  await connection();
  let data: Network;
  try {
    data = await api.network(WINDOW_MINUTES);
  } catch (error) {
    if (error instanceof ApiUnreachable) return <ApiUnreachableNotice />;
    throw error;
  }
  const { nodes, edges } = layout(data);
  const center = nodes[0];
  const maxMessages = Math.max(1, ...edges.map((e) => e.in + e.out));
  const now = new Date();
  const lastSeen = new Map(data.edges.map((e) => [e.peer, e.last_at]));

  return (
    <>
      {!data.registry_ok && (
        <p role="alert" className="rounded-md border border-warn/40 bg-warn-soft px-5 py-3 text-sm">
          Không kết nối được Registry. Danh sách dưới đây là các agent đã biết từ trước, lấy từ bộ nhớ đệm.
        </p>
      )}
      <section aria-labelledby="graph-title" className="rounded-lg border border-line bg-surface p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="graph-title" className="font-bold">
            Message trong {WINDOW_MINUTES} phút qua
          </h2>
          <p className="flex gap-4 text-xs text-muted">
            <span className="flex items-center gap-1.5">
              <span aria-hidden className="h-0.5 w-5 bg-ink/60" /> không lỗi
            </span>
            <span className="flex items-center gap-1.5">
              <span aria-hidden className="h-0.5 w-5 bg-warn" /> có message lỗi
            </span>
          </p>
        </div>
        <svg viewBox={`0 0 ${W} ${H}`} className="mt-2 w-full" role="img" aria-labelledby="graph-desc">
          <desc id="graph-desc">
            {nodes.length - 1} agent quanh {center.id}. Đường càng dày thì càng nhiều message. Chi tiết ở bảng bên dưới.
          </desc>
          {edges.map((e) => {
            const node = nodes.find((n) => n.id === e.peer);
            if (!node) return null;
            const total = e.in + e.out;
            return (
              <g key={e.peer} className="group">
                <line
                  x1={center.x}
                  y1={center.y}
                  x2={node.x}
                  y2={node.y}
                  stroke={e.failed ? "var(--color-warn)" : "var(--color-ink)"}
                  strokeOpacity={e.failed ? 1 : 0.6}
                  strokeWidth={1.5 + (3.5 * total) / maxMessages}
                  strokeLinecap="round"
                />
                <line x1={center.x} y1={center.y} x2={node.x} y2={node.y} stroke="transparent" strokeWidth={16} />
                <text
                  x={(center.x + node.x) / 2}
                  y={(center.y + node.y) / 2 - 6}
                  textAnchor="middle"
                  stroke="var(--color-surface)"
                  strokeWidth={4}
                  paintOrder="stroke"
                  strokeLinejoin="round"
                  className="fill-muted text-[11px]"
                >
                  {total}
                </text>
                <title>
                  {`${e.out} gửi đi, ${e.in} nhận về, ${e.failed} lỗi${e.latency ? `, trung bình ${e.latency} ms` : ""}`}
                </title>
              </g>
            );
          })}
          {nodes.map((n) => (
            <g key={n.id}>
              <circle
                cx={n.x}
                cy={n.y}
                r={n.self ? 30 : 20}
                fill={n.self ? "var(--color-action)" : "var(--color-surface)"}
                stroke={n.known ? "var(--color-ink)" : "var(--color-line)"}
                strokeWidth={2}
                strokeDasharray={n.known ? undefined : "4 3"}
              />
              <text x={n.x} y={n.y + (n.self ? 48 : 36)} textAnchor="middle" stroke="var(--color-surface)" strokeWidth={4} paintOrder="stroke" strokeLinejoin="round" className="fill-ink text-[13px] font-medium">
                {n.label}
              </text>
              <text x={n.x} y={n.y + (n.self ? 64 : 52)} textAnchor="middle" stroke="var(--color-surface)" strokeWidth={4} paintOrder="stroke" strokeLinejoin="round" className="fill-muted text-[11px]">
                {n.sub.length > 34 ? `${n.sub.slice(0, 33)}…` : n.sub}
              </text>
            </g>
          ))}
        </svg>
      </section>

      <section aria-labelledby="agents-title">
        <h2 id="agents-title" className="font-bold">
          {data.nodes.length - 1} agent khác đã biết
        </h2>
        <ul className="mt-3 divide-y divide-line border-y border-line">
          {data.nodes
            .filter((n) => !n.is_self)
            .map((n) => {
              const edge = edges.find((e) => e.peer === n.agent_id);
              return (
                <li key={n.agent_id} className="grid gap-x-6 gap-y-1 py-4 sm:grid-cols-[14rem_1fr]">
                  <div>
                    <p className="font-medium">{n.agent_id}</p>
                    <p className="text-xs text-muted">
                      {n.domain}, {n.source === "cache" ? "từ bộ nhớ đệm" : "từ Registry"}
                    </p>
                  </div>
                  <div>
                    <p className="text-sm">
                      {n.capabilities.join(", ")}
                      <span className="text-muted"> tại {n.endpoint}</span>
                    </p>
                    <p className={`text-xs ${edge?.failed ? "font-medium text-warn" : "text-muted"}`}>
                      {edge
                        ? `${edge.out} message gửi đi, ${edge.failed} lỗi, lần cuối ${formatAgo(lastSeen.get(n.agent_id), now)}`
                        : `Chưa trao đổi message trong ${WINDOW_MINUTES} phút qua`}
                    </p>
                    <TestCall agentId={n.agent_id} capabilities={n.capabilities} />
                  </div>
                </li>
              );
            })}
        </ul>
      </section>
    </>
  );
}
