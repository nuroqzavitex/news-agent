"use client";

import { useActionState } from "react";

import { crawlNow, type CrawlState } from "@/app/news/actions";

export function CrawlNowButton() {
  const [state, action, pending] = useActionState<CrawlState>(crawlNow, null);
  return (
    <form action={action} className="flex items-center gap-3">
      <p role="status" className={`text-sm ${state?.ok === false ? "text-warn" : "text-muted"}`}>
        {state?.message}
      </p>
      <button
        type="submit"
        disabled={pending}
        className="rounded-md border border-line px-3 py-1.5 text-sm font-medium hover:border-ink disabled:opacity-60"
      >
        {pending ? "Đang gửi yêu cầu" : "Thu thập ngay"}
      </button>
    </form>
  );
}
