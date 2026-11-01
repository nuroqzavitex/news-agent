import { API_BASE } from "@/lib/api";

export function ApiUnreachableNotice() {
  return (
    <div role="alert" className="rounded-md border border-warn/40 bg-warn-soft px-5 py-4 text-sm">
      <p className="font-medium">Không kết nối được agent-api tại {API_BASE}.</p>
      <p className="mt-1 text-muted">
        Chạy <code className="rounded bg-surface px-1 py-0.5">docker compose up -d</code> ở thư mục gốc, rồi tải lại
        trang.
      </p>
    </div>
  );
}
