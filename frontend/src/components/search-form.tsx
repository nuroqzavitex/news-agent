import Form from "next/form";

import type { SourceOut } from "@/lib/api";

export function SearchForm({ q = "", source = "", sources = [] }: { q?: string; source?: string; sources?: SourceOut[] }) {
  return (
    <Form action="/news" role="search" className="flex flex-col gap-3 sm:flex-row">
      <label className="flex-1">
        <span className="sr-only">Từ khóa</span>
        <input
          name="q"
          type="search"
          defaultValue={q}
          placeholder="Tìm tin, có dấu hay không dấu đều được: giá vàng, gia vang"
          className="w-full rounded-md border border-line bg-surface px-4 py-2.5 placeholder:text-muted/80"
        />
      </label>
      <label>
        <span className="sr-only">Báo</span>
        <select
          name="source"
          defaultValue={source}
          className="w-full rounded-md border border-line bg-surface px-3 py-2.5 sm:w-44"
        >
          <option value="">Tất cả các báo</option>
          {sources.map((s) => (
            <option key={s.code} value={s.code}>
              {s.name}
            </option>
          ))}
        </select>
      </label>
      <button
        type="submit"
        className="rounded-md bg-action px-5 py-2.5 font-medium text-white hover:bg-action-strong"
      >
        Tìm
      </button>
    </Form>
  );
}
