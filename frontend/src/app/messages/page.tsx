import type { Metadata } from "next";
import Form from "next/form";
import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";

import { ApiUnreachableNotice } from "@/components/api-error";
import { api, ApiUnreachable, type MessageOut, type MessagePage } from "@/lib/api";
import { formatDateTime } from "@/lib/format";

export const metadata: Metadata = { title: "Nhật ký message" };

const PAGE_SIZE = 50;
const STATUS_LABEL: Record<string, string> = {
  received: "đã nhận",
  processing: "đang xử lý",
  replied: "đã trả lời",
  delivered: "đã gửi",
  failed: "lỗi",
};
type Params = { direction?: string; status?: string; peer?: string; capability?: string; page?: string };

export default function MessagesPage({ searchParams }: PageProps<"/messages">) {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-bold tracking-tight">Nhật ký message</h1>
      <Suspense fallback={<Filters params={{}} />}>
        <MessageList searchParams={searchParams as Promise<Params>} />
      </Suspense>
    </div>
  );
}

function first(value: unknown): string {
  return (Array.isArray(value) ? value[0] : typeof value === "string" ? value : "")?.trim() ?? "";
}

const field = "rounded-md border border-line bg-surface px-3 py-2";

function Filters({ params }: { params: Record<string, string> }) {
  return (
    <Form action="/messages" className="grid gap-3 sm:grid-cols-[repeat(4,1fr)_auto]">
      <label>
        <span className="sr-only">Chiều</span>
        <select name="direction" defaultValue={params.direction ?? ""} className={`w-full ${field}`}>
          <option value="">Cả gửi và nhận</option>
          <option value="inbound">Agent này nhận</option>
          <option value="outbound">Agent này gửi</option>
        </select>
      </label>
      <label>
        <span className="sr-only">Trạng thái</span>
        <select name="status" defaultValue={params.status ?? ""} className={`w-full ${field}`}>
          <option value="">Mọi trạng thái</option>
          {Object.entries(STATUS_LABEL).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span className="sr-only">Agent</span>
        <input name="peer" defaultValue={params.peer ?? ""} placeholder="Agent, vd mock-finance-a" className={field} />
      </label>
      <label>
        <span className="sr-only">Capability</span>
        <input name="capability" defaultValue={params.capability ?? ""} placeholder="Capability" className={field} />
      </label>
      <button type="submit" className="rounded-md bg-action px-5 py-2 font-medium text-white hover:bg-action-strong">
        Lọc
      </button>
    </Form>
  );
}

async function MessageList({ searchParams }: { searchParams: Promise<Params> }) {
  await connection();
  const raw = await searchParams;
  const params = Object.fromEntries(
    (["direction", "status", "peer", "capability"] as const).map((k) => [k, first(raw[k])]).filter(([, v]) => v),
  );
  const page = Math.max(1, Number.parseInt(first(raw.page) || "1", 10) || 1);

  let data: MessagePage;
  try {
    data = await api.messages({ ...params, page, page_size: PAGE_SIZE });
  } catch (error) {
    if (error instanceof ApiUnreachable) {
      return (
        <>
          <Filters params={params} />
          <ApiUnreachableNotice />
        </>
      );
    }
    throw error;
  }
  const pages = Math.max(1, Math.ceil(data.total / PAGE_SIZE));
  const href = (p: number) => `/messages?${new URLSearchParams({ ...params, page: String(p) })}`;

  return (
    <>
      <Filters params={params} />
      <p className="text-sm text-muted">{data.total.toLocaleString("vi-VN")} message, mới nhất trước</p>
      {data.items.length === 0 ? (
        <p className="rounded-md border border-dashed border-line px-5 py-8 text-center text-muted">
          Chưa có message nào khớp. Gửi một câu hỏi ở trang Hỏi agent để tạo message giữa các agent.
        </p>
      ) : (
        <ol className="divide-y divide-line border-y border-line">
          {data.items.map((m) => (
            <MessageRow key={m.message_id} message={m} />
          ))}
        </ol>
      )}
      {pages > 1 && (
        <nav aria-label="Phân trang" className="flex items-center justify-between text-sm">
          {page > 1 ? <Link href={href(page - 1)} className="font-medium text-action hover:underline">Trang trước</Link> : <span />}
          <span className="text-muted">Trang {page} / {pages}</span>
          {page < pages ? <Link href={href(page + 1)} className="font-medium text-action hover:underline">Trang sau</Link> : <span />}
        </nav>
      )}
    </>
  );
}

function MessageRow({ message: m }: { message: MessageOut }) {
  const outbound = m.direction === "outbound";
  const failed = m.status === "failed";
  return (
    <li className="py-3">
      <details className="group">
        <summary className="grid cursor-pointer list-none gap-x-4 gap-y-1 sm:grid-cols-[9rem_1fr_auto]">
          <span className="text-xs text-muted">{formatDateTime(m.created_at)}</span>
          <span className="text-sm">
            <span className="font-medium">{outbound ? `Gửi tới ${m.to_agent}` : `Nhận từ ${m.from_agent}`}</span>
            <span className="text-muted">
              {" "}
              {m.type}
              {m.capability && `, ${m.capability}`}
              {m.attempts > 1 && `, ${m.attempts} lần thử`}
            </span>
          </span>
          <span className={`text-sm sm:text-right ${failed ? "font-medium text-warn" : "text-muted"}`}>
            {STATUS_LABEL[m.status] ?? m.status}
            {m.latency_ms !== null && `, ${m.latency_ms} ms`}
          </span>
        </summary>
        <div className="mt-3 flex flex-col gap-2 text-sm sm:ml-[10rem]">
          {m.error && <p className="text-warn">{m.error}</p>}
          <p className="text-xs text-muted">
            message_id <code>{m.message_id}</code>
            {m.reply_to && (
              <>
                , trả lời cho <code>{m.reply_to}</code>
              </>
            )}
          </p>
          <pre className="max-h-64 overflow-auto rounded-md bg-paper p-3 text-xs">{JSON.stringify(m.payload, null, 2)}</pre>
          <Link href={`/traces/${m.trace_id}`} className="text-sm font-medium text-action hover:underline">
            Xem toàn bộ trace
          </Link>
        </div>
      </details>
    </li>
  );
}
