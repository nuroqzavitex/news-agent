"use server";

import { refresh } from "next/cache";

import { api } from "@/lib/api";

export type CrawlState = { message: string; ok: boolean } | null;

export async function crawlNow(): Promise<CrawlState> {
  try {
    const enqueued = await api.triggerCrawl();
    refresh();
    return { ok: true, message: `Đã yêu cầu thu thập ${enqueued.length} báo. Bài mới sẽ xuất hiện sau ít phút.` };
  } catch {
    return { ok: false, message: "Không gửi được yêu cầu thu thập. Kiểm tra agent-api rồi thử lại." };
  }
}
