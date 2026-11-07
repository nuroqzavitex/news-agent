import Form from "next/form";

import type { SourceOut } from "@/lib/api";

const fieldClass = "w-full rounded-md border border-line bg-surface px-3 py-2.5";

export function SearchForm({
  q = "",
  source = "",
  sentiment = "",
  mode = "hybrid",
  sources = [],
}: {
  q?: string;
  source?: string;
  sentiment?: string;
  mode?: string;
  sources?: SourceOut[];
}) {
  return (
    <Form action="/news" role="search" className="grid gap-3 sm:grid-cols-[1fr_auto]">
      <label>
        <span className="sr-only">Từ khóa</span>
        <input
          name="q"
          type="search"
          defaultValue={q}
          placeholder="Tìm tin, có dấu hay không dấu đều được: giá vàng, gia vang"
          className={`${fieldClass} px-4 placeholder:text-muted/80`}
        />
      </label>
      <button type="submit" className="rounded-md bg-action px-5 py-2.5 font-medium text-white hover:bg-action-strong">
        Tìm
      </button>
      <div className="grid gap-3 sm:col-span-2 sm:grid-cols-3">
        <label>
          <span className="sr-only">Báo</span>
          <select name="source" defaultValue={source} className={fieldClass}>
            <option value="">Tất cả các báo</option>
            {sources.map((s) => (
              <option key={s.code} value={s.code}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span className="sr-only">Sắc thái</span>
          <select name="sentiment" defaultValue={sentiment} className={fieldClass}>
            <option value="">Mọi sắc thái</option>
            <option value="positive">Tin tích cực</option>
            <option value="neutral">Tin trung lập</option>
            <option value="negative">Tin tiêu cực</option>
          </select>
        </label>
        <label>
          <span className="sr-only">Cách tìm</span>
          <select name="mode" defaultValue={mode} className={fieldClass}>
            <option value="hybrid">Tìm theo từ khóa và ý nghĩa</option>
            <option value="keyword">Chỉ theo từ khóa</option>
            <option value="semantic">Chỉ theo ý nghĩa</option>
          </select>
        </label>
      </div>
    </Form>
  );
}
