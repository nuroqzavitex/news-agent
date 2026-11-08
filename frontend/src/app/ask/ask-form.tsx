"use client";

import Link from "next/link";
import { useActionState } from "react";

import { askAgent, type AskState } from "@/app/ask/actions";
import type { AskResult, StepResult } from "@/lib/api";

const EXAMPLES = [
  "Tin tức ảnh hưởng thế nào tới cổ phiếu TCB tuần này?",
  "Giá vàng hôm nay thế nào?",
  "Thời tiết ở Đà Nẵng thế nào?",
  "Hôm nay có tin gì nổi bật?",
];

const CAPABILITY_LABEL: Record<string, string> = {
  news_summary: "Tóm tắt tin",
  news_search: "Tìm tin",
  sentiment: "Sắc thái tin",
  trending_topics: "Chủ đề nổi bật",
  stock_price: "Giá cổ phiếu",
  weather: "Thời tiết",
};

const STATUS: Record<StepResult["status"], { label: string; className: string }> = {
  ok: { label: "Có kết quả", className: "text-ok" },
  memory: { label: "Lấy từ bộ nhớ, chưa hết hạn", className: "text-ok" },
  stale: { label: "Dữ liệu cũ, không làm mới được", className: "text-warn" },
  failed: { label: "Các agent đều lỗi", className: "text-warn" },
  missing: { label: "Không có agent nào cung cấp", className: "text-warn" },
};

export function AskForm() {
  const [state, action, pending] = useActionState<AskState, FormData>(askAgent, { result: null, error: null });
  return (
    <div className="flex max-w-3xl flex-col gap-6">
      <form action={action} className="flex flex-col gap-3">
        <label htmlFor="question" className="text-2xl font-bold tracking-tight">
          Hỏi agent về tin tức
        </label>
        <textarea
          id="question"
          name="question"
          rows={2}
          required
          maxLength={1000}
          defaultValue={state.result?.question}
          placeholder={EXAMPLES[0]}
          className="w-full rounded-md border border-line bg-surface px-4 py-3 placeholder:text-muted/80"
        />
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <button
            type="submit"
            disabled={pending}
            className="rounded-md bg-action px-5 py-2.5 font-medium text-white hover:bg-action-strong disabled:opacity-60"
          >
            {pending ? "Đang hỏi các agent" : "Gửi câu hỏi"}
          </button>
          {EXAMPLES.slice(1).map((example) => (
            <button
              key={example}
              type="submit"
              name="question"
              value={example}
              disabled={pending}
              formNoValidate
              className="text-sm text-action hover:underline disabled:opacity-60"
            >
              {example}
            </button>
          ))}
        </div>
      </form>

      {state.error && (
        <p role="alert" className="rounded-md border border-warn/40 bg-warn-soft px-5 py-4 text-sm">
          {state.error}
        </p>
      )}
      {state.result && <Answer result={state.result} />}
    </div>
  );
}

function Answer({ result }: { result: AskResult }) {
  return (
    <section aria-label="Câu trả lời" className="flex flex-col gap-5" aria-live="polite">
      <div className="rounded-lg border border-line bg-surface p-5">
        <p className="leading-[1.75] whitespace-pre-line">{result.answer}</p>
        <p className="mt-3 text-sm text-muted">
          {result.generated_by === "template"
            ? "Ghép từ kết quả các agent, chưa dùng LLM."
            : `Viết bởi ${result.generated_by}.`}
        </p>
      </div>

      {(result.partial || result.has_conflict || result.has_stale_data) && (
        <ul className="flex flex-col gap-1 rounded-md bg-warn-soft px-5 py-3 text-sm">
          {result.partial && <li>Câu trả lời chưa đầy đủ: thiếu {result.missing.map(labelOf).join(", ")}.</li>}
          {result.has_conflict && <li>Các agent báo số liệu khác nhau, xem chi tiết bên dưới.</li>}
          {result.has_stale_data && <li>Một phần dữ liệu đã hết hạn và không làm mới được.</li>}
        </ul>
      )}

      <div>
        <h2 className="font-bold">{result.agents.length} agent đã tham gia</h2>
        <p className="mt-1 text-sm text-muted">{result.agents.join(", ")}</p>
        <table className="mt-3 w-full border-t border-line text-sm">
          <caption className="sr-only">Kết quả từng bước khi trả lời câu hỏi</caption>
          <thead className="sr-only">
            <tr>
              <th scope="col">Việc</th>
              <th scope="col">Kết quả</th>
              <th scope="col">Thời gian</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line border-b border-line">
            {result.steps.map((step) => (
              <StepRow key={`${step.capability}-${JSON.stringify(step.request)}`} step={step} />
            ))}
          </tbody>
        </table>
        <Link href={`/traces/${result.trace_id}`} className="mt-3 inline-block text-sm font-medium text-action hover:underline">
          Xem trace: các agent đã được gọi thế nào
        </Link>
      </div>
    </section>
  );
}

function labelOf(capability: string): string {
  return CAPABILITY_LABEL[capability] ?? capability;
}

function StepRow({ step }: { step: StepResult }) {
  const status = STATUS[step.status];
  const conflicts = step.conflicts ?? [];
  return (
    <tr className="align-top">
      <th scope="row" className="py-2.5 pr-4 text-left font-medium">
        {labelOf(step.capability)}
      </th>
      <td className="py-2.5 pr-4">
        <span className={`font-medium ${status.className}`}>{status.label}</span>
        {(step.agents ?? []).length > 0 && <span className="text-muted">, từ {step.agents?.join(" và ")}</span>}
        {step.error && <p className="mt-1 text-xs break-words text-muted">{step.error}</p>}
        {conflicts.map((conflict) => (
          <p key={conflict.agent} className="mt-1 text-xs text-warn">
            {conflict.agent} báo khác: {describe(conflict.data)}
          </p>
        ))}
      </td>
      <td className="py-2.5 text-right whitespace-nowrap text-muted">{(step.latency_ms ?? 0).toLocaleString("vi-VN")} ms</td>
    </tr>
  );
}

function describe(data: Record<string, unknown>): string {
  if (typeof data.price === "number") return `${data.price.toLocaleString("vi-VN")} ${data.currency ?? ""}`.trim();
  return JSON.stringify(data);
}
