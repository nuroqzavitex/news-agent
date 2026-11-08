import type { Metadata } from "next";
import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";

import { ApiUnreachableNotice } from "@/components/api-error";
import { LatencyChart, RequestsChart } from "@/components/charts";
import { CrawlGrid, CrawlGridFallback } from "@/components/crawl-grid";
import { api, ApiUnreachable, type Health, type Metrics } from "@/lib/api";

// The root page shares the root layout's segment, so the title template does not apply here.
export const metadata: Metadata = { title: { absolute: "Tổng quan | News Agent" } };

const WINDOW_MINUTES = 60;

export default function OverviewPage() {
  return (
    <div className="flex flex-col gap-8">
      <Suspense fallback={<div aria-hidden className="h-[420px] rounded-lg border border-line bg-surface" />}>
        <Overview />
      </Suspense>
      <Suspense fallback={<CrawlGridFallback />}>
        <CrawlGrid />
      </Suspense>
    </div>
  );
}

const DEP_LABEL: Record<string, string> = {
  db: "PostgreSQL",
  redis: "Redis",
  crawler: "Crawler",
  llm: "Gemini",
  registry: "Registry (Team 1)",
  broker: "Broker (Team 2)",
  identity: "Identity (Team 3)",
  tracer: "Observability (Team 4)",
};

const FAULT_LABEL: Record<string, string> = {
  inbound_delay_s: "agent trả lời chậm",
  inbound_fail_status: "agent trả lỗi",
  llm_down: "Gemini down",
  registry_down: "Registry down",
  identity_down: "Identity down",
  tracer_down: "Observability down",
};

function depState(name: string, value: string): { ok: boolean; text: string } {
  if (value === "ok") return { ok: true, text: "hoạt động" };
  if (name === "llm" && value === "not_configured") return { ok: true, text: "chưa có key, dùng fallback" };
  if (value === "never_ran") return { ok: false, text: "chưa chạy lần nào" };
  if (value.startsWith("stale")) return { ok: false, text: "dữ liệu cũ, crawler không chạy" };
  if (value === "down") return { ok: false, text: "không kết nối được" };
  return { ok: false, text: value };
}

async function Overview() {
  await connection();
  let health: Health;
  let metrics: Metrics;
  try {
    [health, metrics] = await Promise.all([api.health(), api.metrics(WINDOW_MINUTES)]);
  } catch (error) {
    if (error instanceof ApiUnreachable) return <ApiUnreachableNotice />;
    throw error;
  }

  const kpis = [
    { label: "request mỗi phút", value: metrics.requests_per_minute.toLocaleString("vi-VN") },
    { label: "độ trễ p95", value: metrics.p95_ms === null ? "chưa có" : `${Math.round(metrics.p95_ms)} ms` },
    { label: "tỉ lệ lỗi", value: `${(metrics.error_rate * 100).toLocaleString("vi-VN", { maximumFractionDigits: 1 })}%` },
    { label: "lần retry khi gọi agent khác", value: metrics.retries.toLocaleString("vi-VN") },
  ];

  return (
    <>
      {health.faults.length > 0 && (
        <p role="alert" className="rounded-md border border-warn/40 bg-warn-soft px-5 py-3 text-sm">
          Đang bật lỗi giả lập: {health.faults.map((f) => FAULT_LABEL[f] ?? f).join(", ")}.{" "}
          <Link href="/faults" className="font-medium text-action hover:underline">
            Tắt ở Fault lab
          </Link>
        </p>
      )}

      <section aria-labelledby="deps-title" className="rounded-lg border border-line bg-surface p-5">
        <h2 id="deps-title" className="font-bold">
          Các thành phần agent đang dùng
        </h2>
        <ul className="mt-4 grid gap-x-6 gap-y-3 sm:grid-cols-2 lg:grid-cols-4">
          {Object.entries(DEP_LABEL).map(([name, label]) => {
            const state = depState(name, health.deps[name] ?? "unknown");
            const mode = health.modes[name];
            return (
              <li key={name} className="flex gap-2.5">
                <span aria-hidden className={`mt-1.5 size-2.5 shrink-0 rounded-full ${state.ok ? "bg-ok" : "bg-warn"}`} />
                <span>
                  <span className="block text-sm font-medium">{label}</span>
                  <span className={`block text-xs ${state.ok ? "text-muted" : "font-medium text-warn"}`}>
                    {state.text}
                    {mode && `, bản ${mode === "local" ? "local" : "của nhóm"}`}
                  </span>
                </span>
              </li>
            );
          })}
        </ul>
      </section>

      <section aria-labelledby="traffic-title" className="rounded-lg border border-line bg-surface p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="traffic-title" className="font-bold">
            {metrics.inbound_requests.toLocaleString("vi-VN")} request từ agent khác trong {WINDOW_MINUTES} phút qua
          </h2>
          <Link href="/messages" className="text-sm font-medium text-action hover:underline">
            Xem nhật ký message
          </Link>
        </div>
        <dl className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
          {kpis.map((kpi) => (
            <div key={kpi.label}>
              <dt className="text-xs text-muted">{kpi.label}</dt>
              <dd className="text-2xl font-bold tracking-tight">{kpi.value}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-3 text-xs text-muted">
          {metrics.outbound_calls} lần gọi agent khác ({metrics.outbound_failed} thất bại), {metrics.duplicates} message
          trùng được trả lại reply cũ, {metrics.rejected} message bị từ chối vì token không hợp lệ.
        </p>
        {metrics.inbound_requests > 0 ? (
          <div className="mt-6 grid gap-8 lg:grid-cols-2">
            <RequestsChart points={metrics.series} />
            <LatencyChart points={metrics.series} />
          </div>
        ) : (
          <p className="mt-6 rounded-md border border-dashed border-line px-5 py-8 text-center text-sm text-muted">
            Chưa có agent nào gọi tới agent này trong {WINDOW_MINUTES} phút qua, nên chưa có biểu đồ. Thử gửi message
            bằng <code>scripts/send.py</code> hoặc từ trang Mạng agent của một agent khác.
          </p>
        )}
        {metrics.by_capability.length > 0 && (
          <table className="mt-6 w-full text-sm">
            <caption className="sr-only">Số request và độ trễ theo capability</caption>
            <thead className="text-left text-xs text-muted">
              <tr>
                <th scope="col" className="pb-2 font-normal">Capability</th>
                <th scope="col" className="pb-2 text-right font-normal">Request</th>
                <th scope="col" className="pb-2 text-right font-normal">Lỗi</th>
                <th scope="col" className="pb-2 text-right font-normal">p50</th>
                <th scope="col" className="pb-2 text-right font-normal">p95</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line border-t border-line">
              {metrics.by_capability.map((c) => (
                <tr key={c.capability}>
                  <th scope="row" className="py-2 text-left font-medium">{c.capability}</th>
                  <td className="py-2 text-right">{c.requests}</td>
                  <td className={`py-2 text-right ${c.errors ? "font-medium text-warn" : ""}`}>{c.errors}</td>
                  <td className="py-2 text-right">{c.p50_ms === null ? "–" : `${Math.round(c.p50_ms)} ms`}</td>
                  <td className="py-2 text-right">{c.p95_ms === null ? "–" : `${Math.round(c.p95_ms)} ms`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}
