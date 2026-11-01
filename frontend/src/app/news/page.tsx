import type { Metadata } from "next";
import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";

import { ApiUnreachableNotice } from "@/components/api-error";
import { ArticleList } from "@/components/article-list";
import { CrawlGrid, CrawlGridFallback } from "@/components/crawl-grid";
import { SearchForm } from "@/components/search-form";
import { api, ApiUnreachable, type SearchResult, type SourceOut } from "@/lib/api";

export const metadata: Metadata = { title: "Tin tức" };

const PAGE_SIZE = 20;

type Params = { q?: string; source?: string; page?: string };

export default function NewsPage({ searchParams }: PageProps<"/news">) {
  return (
    <div className="flex flex-col gap-8">
      <Suspense fallback={<CrawlGridFallback />}>
        <CrawlGrid />
      </Suspense>
      <Suspense fallback={<SearchForm />}>
        <SearchSection searchParams={searchParams as Promise<Params>} />
      </Suspense>
    </div>
  );
}

function first(value: string | string[] | undefined): string {
  return (Array.isArray(value) ? value[0] : value)?.trim() ?? "";
}

async function SearchSection({ searchParams }: { searchParams: Promise<Params> }) {
  await connection();
  const params = await searchParams;
  const q = first(params.q);
  const source = first(params.source);
  const page = Math.max(1, Number.parseInt(first(params.page) || "1", 10) || 1);

  let result: SearchResult;
  let sources: SourceOut[];
  try {
    [result, sources] = await Promise.all([
      api.articles({ q: q || undefined, source: source || undefined, page, page_size: PAGE_SIZE }),
      api.sources(),
    ]);
  } catch (error) {
    if (!(error instanceof ApiUnreachable)) throw error;
    return (
      <section className="flex flex-col gap-5">
        <SearchForm q={q} source={source} />
        <ApiUnreachableNotice />
      </section>
    );
  }

  const sourceNames = Object.fromEntries(sources.map((s) => [s.code, s.name]));
  const pages = Math.max(1, Math.ceil(result.count / PAGE_SIZE));
  const pageHref = (p: number) => {
    const search = new URLSearchParams({ ...(q && { q }), ...(source && { source }), page: String(p) });
    return `/news?${search}`;
  };

  return (
    <section aria-labelledby="results-title" className="flex flex-col gap-5">
      <SearchForm q={q} source={source} sources={sources} />

      <h2 id="results-title" className="text-sm text-muted">
        {q
          ? `${result.count.toLocaleString("vi-VN")} bài khớp với “${q}”`
          : `${result.count.toLocaleString("vi-VN")} bài, mới nhất trước`}
        {source && ` trên ${sourceNames[source] ?? source}`}
      </h2>

      {result.results.length > 0 ? (
        <ArticleList items={result.results} sourceNames={sourceNames} now={new Date()} />
      ) : (
        <p className="rounded-md border border-dashed border-line px-5 py-8 text-center text-muted">
          {q
            ? "Không có bài nào khớp. Thử từ khóa ngắn hơn hoặc bỏ lọc theo báo."
            : "Chưa có bài nào. Bấm “Thu thập ngay” ở trên, bài đầu tiên sẽ xuất hiện sau khoảng một phút."}
        </p>
      )}

      {pages > 1 && (
        <nav aria-label="Phân trang" className="flex items-center justify-between text-sm">
          {page > 1 ? (
            <Link href={pageHref(page - 1)} className="font-medium text-action hover:underline">
              Trang trước
            </Link>
          ) : (
            <span />
          )}
          <span className="text-muted">
            Trang {page} / {pages}
          </span>
          {page < pages ? (
            <Link href={pageHref(page + 1)} className="font-medium text-action hover:underline">
              Trang sau
            </Link>
          ) : (
            <span />
          )}
        </nav>
      )}
    </section>
  );
}
