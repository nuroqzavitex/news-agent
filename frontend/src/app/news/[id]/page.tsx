import Link from "next/link";
import { notFound } from "next/navigation";
import { connection } from "next/server";
import { Suspense } from "react";

import { ApiUnreachableNotice } from "@/components/api-error";
import { api, ApiUnreachable, type ArticleDetail } from "@/lib/api";
import { formatDateTime, SENTIMENT_LABEL } from "@/lib/format";

export default function ArticlePage({ params }: PageProps<"/news/[id]">) {
  return (
    <div className="flex flex-col gap-6">
      <Link href="/news" className="text-sm font-medium text-action hover:underline">
        Về danh sách tin
      </Link>
      <Suspense fallback={<div aria-hidden className="h-96 rounded-lg bg-surface" />}>
        <Article params={params} />
      </Suspense>
    </div>
  );
}

async function Article({ params }: { params: Promise<{ id: string }> }) {
  await connection();
  const { id } = await params;
  let article: ArticleDetail | null;
  let sourceName: string;
  try {
    article = await api.article(id);
    if (!article) notFound();
    const sources = await api.sources();
    sourceName = sources.find((s) => s.code === article!.source)?.name ?? article.source;
  } catch (error) {
    if (error instanceof ApiUnreachable) return <ApiUnreachableNotice />;
    throw error;
  }

  const paragraphs = (article.content ?? "").split("\n").filter(Boolean);

  return (
    <article className="max-w-[68ch]">
      <p className="text-sm text-muted">
        <span className="font-medium text-ink">{sourceName}</span>
        {article.published_at && `, đăng lúc ${formatDateTime(article.published_at)}`}
        {article.sentiment_label && SENTIMENT_LABEL[article.sentiment_label] && (
          <span className={`font-medium ${article.sentiment_label === "positive" ? "text-ok" : "text-warn"}`}>
            {" "}
            ({SENTIMENT_LABEL[article.sentiment_label].toLowerCase()})
          </span>
        )}
      </p>
      <h1 className="mt-2 text-3xl leading-tight font-bold tracking-tight text-balance">{article.title}</h1>
      {article.summary && <p className="mt-4 text-lg leading-relaxed font-medium">{article.summary}</p>}

      {article.duplicate_of && (
        <p className="mt-5 rounded-md bg-surface px-4 py-3 text-sm">
          Nội dung gần như trùng với{" "}
          <Link href={`/news/${article.duplicate_of}`} className="font-medium text-action hover:underline">
            một bài đã lưu trước đó
          </Link>
          , nên bài này không xuất hiện trong kết quả tìm kiếm.
        </p>
      )}

      {paragraphs.length > 0 ? (
        <div className="mt-6 flex flex-col gap-4 leading-[1.75]">
          {paragraphs.map((p, i) => (
            <p key={i}>{p}</p>
          ))}
        </div>
      ) : (
        <p className="mt-6 rounded-md bg-warn-soft px-4 py-3 text-sm">
          Agent chỉ lưu được phần tóm tắt từ RSS, không tải được toàn văn bài này. Đọc đầy đủ trên trang gốc.
        </p>
      )}

      <p className="mt-8 border-t border-line pt-4 text-sm">
        <a href={article.url} target="_blank" rel="noreferrer" className="font-medium text-action hover:underline">
          Đọc bài gốc trên {sourceName}
        </a>
        <span className="text-muted">, thu thập lúc {formatDateTime(article.crawled_at)}</span>
      </p>
    </article>
  );
}
