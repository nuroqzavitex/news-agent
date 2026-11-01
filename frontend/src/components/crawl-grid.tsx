import { connection } from "next/server";

import { CrawlNowButton } from "@/components/crawl-now-button";
import { api, ApiUnreachable, type CrawlGrid as Grid } from "@/lib/api";
import { formatAgo, formatHour } from "@/lib/format";

const HOURS = 24;

/** On narrow screens only the most recent 12 hours are shown, so nothing needs horizontal scrolling. */
const olderHalf = (i: number) => i < HOURS / 2;

/** Cell colour: ink with opacity by volume; amber when most articles of the hour lack full text. */
function cellStyle(count: number, partial: number, max: number): React.CSSProperties {
  if (count === 0) return {};
  const strength = 0.25 + 0.75 * Math.sqrt(count / max);
  const colour = partial * 2 > count ? "var(--color-warn)" : "var(--color-ink)";
  return { backgroundColor: `color-mix(in srgb, ${colour} ${Math.round(strength * 100)}%, transparent)` };
}

export async function CrawlGrid() {
  await connection();
  let grid: Grid;
  try {
    grid = await api.crawlGrid(HOURS);
  } catch (error) {
    if (error instanceof ApiUnreachable) return null; // the results section shows the connection notice
    throw error;
  }
  const now = new Date();
  const max = Math.max(1, ...grid.sources.flatMap((s) => s.counts));
  const total = grid.sources.reduce((sum, s) => sum + s.counts.reduce((a, b) => a + b, 0), 0);

  return (
    <section aria-labelledby="crawl-grid-title" className="rounded-lg border border-line bg-surface p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 id="crawl-grid-title" className="font-bold">
          {total.toLocaleString("vi-VN")} bài thu thập trong 24 giờ qua
        </h2>
        <CrawlNowButton />
      </div>

      <div className="relative mt-4 overflow-x-auto">
        <table className="w-full table-fixed sm:min-w-[640px] border-separate border-spacing-[3px] text-sm">
          <caption className="sr-only">
            Số bài lưu được theo từng báo và từng giờ. Ô màu hổ phách: phần lớn bài trong giờ đó chỉ có tóm tắt.
          </caption>
          <thead>
            <tr>
              <th scope="col" className="w-24 text-left font-medium text-muted sm:w-28">
                <span className="sr-only">Báo</span>
              </th>
              {grid.hours.map((hour, i) => (
                <th
                  key={hour}
                  scope="col"
                  className={`${olderHalf(i) ? "hidden sm:table-cell" : ""} text-left text-xs font-normal whitespace-nowrap text-muted`}
                >
                  {i % 6 === 0 || i === grid.hours.length - 1 ? formatHour(hour) : ""}
                </th>
              ))}
              <th scope="col" className="w-24 pl-3 text-left text-xs font-normal text-muted sm:w-44">
                Lần thu thập cuối
              </th>
            </tr>
          </thead>
          <tbody>
            {grid.sources.map((source) => {
              const failing = source.last_status?.startsWith("error");
              return (
                <tr key={source.code}>
                  <th scope="row" className="pr-2 text-left font-medium whitespace-nowrap">
                    {source.name}
                  </th>
                  {source.counts.map((count, i) => (
                    <td
                      key={grid.hours[i]}
                      className={`${olderHalf(i) ? "hidden sm:table-cell" : ""} h-6 rounded-[3px] bg-paper`}
                      style={cellStyle(count, source.partial[i], max)}
                      title={`${source.name}, ${formatHour(grid.hours[i])}: ${count} bài${
                        source.partial[i] ? `, ${source.partial[i]} bài chỉ có tóm tắt` : ""
                      }`}
                    >
                      <span className="sr-only">{count}</span>
                    </td>
                  ))}
                  <td className={`pl-3 text-xs sm:whitespace-nowrap ${failing ? "font-medium text-warn" : "text-muted"}`}>
                    {failing ? "Lỗi kết nối, " : ""}
                    {formatAgo(source.last_crawled_at, now)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function CrawlGridFallback() {
  return <div aria-hidden className="h-[268px] rounded-lg border border-line bg-surface" />;
}
