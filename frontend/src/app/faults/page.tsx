import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { connection } from "next/server";
import { Suspense } from "react";

import { ApiUnreachableNotice } from "@/components/api-error";
import { AdminUnauthorized, adminJson } from "@/lib/admin";
import { ApiUnreachable, type FaultState, type MockAgentState } from "@/lib/api";

import { logout } from "../login/actions";
import { AgentFaultsForm, MockFaultForm } from "./fault-forms";

export const metadata: Metadata = { title: "Fault lab" };

const CONTAINER_FAULTS = [
  { what: "Crawler chết", command: "docker compose stop crawler", see: "Sau 30 phút /health báo crawler cũ; tìm tin vẫn trả bài đã có kèm freshness." },
  { what: "Processor chết giữa chừng", command: "docker compose kill processor", see: "Chạy lại thì message chưa xử lý được nhận lại, không có bài trùng." },
  { what: "Mock agent tắt hẳn", command: "docker compose stop mock-finance-a mock-finance-b", see: "Câu hỏi về cổ phiếu vẫn được trả lời, nhưng báo thiếu giá." },
];

export default function FaultsPage() {
  return (
    <div className="flex max-w-4xl flex-col gap-8">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Fault lab</h1>
          <p className="mt-1 text-sm text-muted">
            Bật lỗi giả lập theo bảng §9 của spec, rồi hỏi agent ở trang{" "}
            <Link href="/ask" className="font-medium text-action hover:underline">Hỏi agent</Link> và xem trace để thấy
            retry, failover và fallback.
          </p>
        </div>
        <form action={logout}>
          <button type="submit" className="text-sm font-medium text-action hover:underline">Đăng xuất</button>
        </form>
      </div>
      <Suspense fallback={<div aria-hidden className="h-96 rounded-lg border border-line bg-surface" />}>
        <Lab />
      </Suspense>
    </div>
  );
}

async function Lab() {
  await connection();
  let faults: FaultState;
  let mocks: MockAgentState[];
  try {
    [faults, mocks] = await Promise.all([
      adminJson<FaultState>("/api/v1/admin/faults"),
      adminJson<MockAgentState[]>("/api/v1/admin/mocks"),
    ]);
  } catch (error) {
    if (error instanceof AdminUnauthorized) redirect("/login?next=/faults");
    if (error instanceof ApiUnreachable) return <ApiUnreachableNotice />;
    throw error;
  }

  return (
    <>
      {!faults.enabled && (
        <p role="alert" className="rounded-md border border-warn/40 bg-warn-soft px-5 py-3 text-sm">
          Lỗi giả lập đang tắt (FAULTS_ENABLED=false). Bật trong .env khi cần demo.
        </p>
      )}
      <section aria-labelledby="self-title" className="rounded-lg border border-line bg-surface p-5">
        <h2 id="self-title" className="font-bold">Agent này và hạ tầng nó dùng</h2>
        <p className="mt-1 mb-4 text-sm text-muted">
          {faults.active.length ? `Đang bật ${faults.active.length} lỗi.` : "Không có lỗi nào đang bật."}
        </p>
        <AgentFaultsForm config={faults.config} enabled={faults.enabled} />
      </section>

      <section aria-labelledby="mocks-title" className="rounded-lg border border-line bg-surface p-5">
        <h2 id="mocks-title" className="font-bold">Agent giả lập của nhóm khác</h2>
        {mocks.length === 0 ? (
          <p className="mt-2 text-sm text-muted">
            Chưa có mock agent nào. Chạy với COMPOSE_PROFILES=mock và LOCAL_REGISTRY_FILE=local_registry.mock.yaml.
          </p>
        ) : (
          <div className="mt-2 divide-y divide-line">
            {mocks.map((m) => (
              <MockFaultForm key={m.agent_id} mock={m} enabled={faults.enabled} />
            ))}
          </div>
        )}
      </section>

      <section aria-labelledby="containers-title">
        <h2 id="containers-title" className="font-bold">Lỗi ở mức container</h2>
        <p className="mt-1 text-sm text-muted">Dashboard không điều khiển Docker; chạy các lệnh này ở thư mục gốc dự án.</p>
        <ul className="mt-3 divide-y divide-line border-y border-line">
          {CONTAINER_FAULTS.map((f) => (
            <li key={f.what} className="grid gap-x-6 gap-y-1 py-3 sm:grid-cols-[14rem_1fr]">
              <span className="text-sm font-medium">{f.what}</span>
              <span className="text-sm">
                <code className="rounded bg-surface px-1.5 py-0.5 text-xs">{f.command}</code>
                <span className="mt-1 block text-xs text-muted">{f.see}</span>
              </span>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}
