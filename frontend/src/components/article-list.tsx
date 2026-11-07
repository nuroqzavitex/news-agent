import Link from "next/link";

import type { SearchHit } from "@/lib/api";
import { formatPublished, SENTIMENT_LABEL } from "@/lib/format";

export function ArticleList({
  items,
  sourceNames,
  now,
}: {
  items: SearchHit[];
  sourceNames: Record<string, string>;
  now: Date;
}) {
  return (
    <ol className="divide-y divide-line border-y border-line">
      {items.map((hit) => (
        <li key={hit.id} className="grid gap-x-6 gap-y-1 py-4 sm:grid-cols-[7.5rem_1fr]">
          <p className="text-sm text-muted">
            <span className="font-medium text-ink">{sourceNames[hit.source] ?? hit.source}</span>
            <br className="hidden sm:block" /> <time dateTime={hit.published_at ?? undefined}>{formatPublished(hit.published_at, now)}</time>
          </p>
          <div className="max-w-[68ch]">
            <h3 className="font-bold leading-snug">
              <Link href={`/news/${hit.id}`} className="hover:text-action hover:underline">
                {hit.title}
              </Link>
            </h3>
            {hit.snippet && <p className="mt-1 line-clamp-2 text-sm leading-relaxed text-muted">{hit.snippet}</p>}
            {hit.sentiment && SENTIMENT_LABEL[hit.sentiment] && (
              <p className={`mt-1 text-xs font-medium ${hit.sentiment === "positive" ? "text-ok" : "text-warn"}`}>
                {SENTIMENT_LABEL[hit.sentiment]}
              </p>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}
