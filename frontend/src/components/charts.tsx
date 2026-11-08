"use client";

import { useState } from "react";

export type Point = { start: string; requests: number; errors: number; p95_ms: number | null };

const timeFmt = new Intl.DateTimeFormat("vi-VN", { timeZone: "Asia/Ho_Chi_Minh", hour: "2-digit", minute: "2-digit" });

function time(iso: string): string {
  return timeFmt.format(new Date(iso));
}

function niceMax(value: number): number {
  if (value <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  return [1, 2, 5, 10].map((m) => m * magnitude).find((m) => m >= value) ?? value;
}

function TimeAxis({ points }: { points: Point[] }) {
  const last = points.length - 1;
  return (
    <div className="mt-1 flex justify-between text-xs text-muted">
      <span>{time(points[0].start)}</span>
      <span>{time(points[Math.floor(last / 2)].start)}</span>
      <span>{time(points[last].start)}</span>
    </div>
  );
}

function Tooltip({ index, count, children }: { index: number; count: number; children: React.ReactNode }) {
  const left = ((index + 0.5) / count) * 100;
  return (
    <div
      role="status"
      className="pointer-events-none absolute -top-2 z-10 -translate-y-full rounded-md border border-line bg-surface px-3 py-2 text-xs whitespace-nowrap shadow-sm"
      style={{ left: `${left}%`, transform: `translate(${left > 70 ? "-100%" : left < 30 ? "0" : "-50%"}, -100%)` }}
    >
      {children}
    </div>
  );
}

/** Requests per time bucket, errors stacked on top in the warning colour. */
export function RequestsChart({ points }: { points: Point[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = niceMax(Math.max(...points.map((p) => p.requests)));
  const hovered = hover === null ? null : points[hover];
  return (
    <figure>
      <figcaption className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="font-bold">Request nhận được</span>
        <span className="flex gap-4 text-xs text-muted">
          <span className="flex items-center gap-1.5">
            <span aria-hidden className="size-2.5 rounded-sm bg-action" /> thành công
          </span>
          <span className="flex items-center gap-1.5">
            <span aria-hidden className="size-2.5 rounded-sm bg-warn" /> lỗi
          </span>
        </span>
      </figcaption>
      <div className="relative mt-3" onMouseLeave={() => setHover(null)}>
        <span className="absolute -top-1 left-0 text-xs text-muted">{max}</span>
        <div aria-hidden className="flex h-32 items-end gap-[2px] border-b border-line pt-4">
          {points.map((p, i) => {
            const ok = p.requests - p.errors;
            return (
              <div
                key={p.start}
                onMouseEnter={() => setHover(i)}
                className={`flex h-full flex-1 flex-col justify-end gap-[2px] ${hover === i ? "bg-paper" : ""}`}
              >
                {p.errors > 0 && (
                  <div className="rounded-t-[4px] bg-warn" style={{ height: `${(p.errors / max) * 100}%` }} />
                )}
                {ok > 0 && (
                  <div
                    className={`bg-action ${p.errors > 0 ? "" : "rounded-t-[4px]"}`}
                    style={{ height: `${(ok / max) * 100}%` }}
                  />
                )}
              </div>
            );
          })}
        </div>
        {hovered && hover !== null && (
          <Tooltip index={hover} count={points.length}>
            <p className="font-medium">{time(hovered.start)}</p>
            <p>
              {hovered.requests} request, {hovered.errors} lỗi
            </p>
          </Tooltip>
        )}
        <TimeAxis points={points} />
      </div>
      <DataTable points={points} />
    </figure>
  );
}

/** p95 latency per time bucket. Buckets without requests leave a gap rather than a fake zero. */
export function LatencyChart({ points }: { points: Point[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const values = points.map((p) => p.p95_ms);
  const max = niceMax(Math.max(0, ...values.filter((v): v is number => v !== null)));
  const x = (i: number) => ((i + 0.5) / points.length) * 100;
  const y = (v: number) => 100 - (v / max) * 100;

  const segments: string[] = [];
  let current = "";
  values.forEach((v, i) => {
    if (v === null) {
      if (current) segments.push(current);
      current = "";
      return;
    }
    current += `${current ? "L" : "M"}${x(i)},${y(v)} `;
  });
  if (current) segments.push(current);

  const hovered = hover === null ? null : points[hover];
  return (
    <figure>
      <figcaption className="font-bold">Độ trễ p95 (ms)</figcaption>
      <div className="relative mt-3" onMouseLeave={() => setHover(null)}>
        <span className="absolute -top-1 left-0 text-xs text-muted">{max}</span>
        <div className="relative h-32 border-b border-line pt-4">
          <svg aria-hidden viewBox="0 0 100 100" preserveAspectRatio="none" className="size-full overflow-visible">
            {segments.map((d) => (
              <path
                key={d}
                d={d}
                fill="none"
                stroke="var(--color-action)"
                strokeWidth={2}
                vectorEffect="non-scaling-stroke"
                strokeLinejoin="round"
              />
            ))}
            {hover !== null && (
              <line
                x1={x(hover)}
                x2={x(hover)}
                y1={0}
                y2={100}
                stroke="var(--color-line)"
                strokeWidth={1}
                vectorEffect="non-scaling-stroke"
              />
            )}
          </svg>
          {values.map((v, i) =>
            v !== null && (hover === i || segments.length === 0 || isolated(values, i)) ? (
              <span
                key={points[i].start}
                aria-hidden
                className="absolute size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-surface bg-action"
                style={{ left: `${x(i)}%`, top: `calc(1rem + (100% - 1rem) * ${y(v) / 100})` }}
              />
            ) : null,
          )}
          <div aria-hidden className="absolute inset-0 flex">
            {points.map((p, i) => (
              <div key={p.start} className="flex-1" onMouseEnter={() => setHover(i)} />
            ))}
          </div>
        </div>
        {hovered && hover !== null && (
          <Tooltip index={hover} count={points.length}>
            <p className="font-medium">{time(hovered.start)}</p>
            <p>{hovered.p95_ms === null ? "không có request" : `p95 ${hovered.p95_ms.toLocaleString("vi-VN")} ms`}</p>
          </Tooltip>
        )}
        <TimeAxis points={points} />
      </div>
    </figure>
  );
}

/** A point with no neighbours draws no line, so it needs a marker to be visible at all. */
function isolated(values: (number | null)[], i: number): boolean {
  return (values[i - 1] ?? null) === null && (values[i + 1] ?? null) === null;
}

function DataTable({ points }: { points: Point[] }) {
  return (
    <table className="sr-only">
      <caption>Số request, lỗi và độ trễ p95 theo từng khoảng thời gian</caption>
      <thead>
        <tr>
          <th scope="col">Bắt đầu</th>
          <th scope="col">Request</th>
          <th scope="col">Lỗi</th>
          <th scope="col">p95 (ms)</th>
        </tr>
      </thead>
      <tbody>
        {points.map((p) => (
          <tr key={p.start}>
            <td>{time(p.start)}</td>
            <td>{p.requests}</td>
            <td>{p.errors}</td>
            <td>{p.p95_ms ?? "không có"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
