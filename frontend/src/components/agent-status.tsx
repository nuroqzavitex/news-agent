import { connection } from "next/server";

import { api, ApiUnreachable, type Health } from "@/lib/api";

const LABEL: Record<Health["status"], string> = {
  online: "Đang hoạt động",
  degraded: "Hoạt động hạn chế",
  offline: "Mất kết nối cơ sở dữ liệu",
};

const DOT: Record<Health["status"] | "unreachable", string> = {
  online: "bg-ok",
  degraded: "bg-warn",
  offline: "bg-red-700",
  unreachable: "bg-red-700",
};

/** Names of dependencies that are not healthy, e.g. "crawler: stale (2400s)". */
function problems(health: Health): string[] {
  return Object.entries(health.deps)
    .filter(([name, value]) => value !== "ok" && !(name === "llm" && value === "not_configured"))
    .map(([name, value]) => `${name}: ${value}`);
}

export async function AgentStatus() {
  await connection();
  let health: Health;
  try {
    health = await api.health();
  } catch (error) {
    if (!(error instanceof ApiUnreachable)) throw error;
    return <StatusLine dot={DOT.unreachable} label="Không kết nối được agent-api" />;
  }
  const issues = problems(health);
  return (
    <StatusLine
      dot={DOT[health.status]}
      label={LABEL[health.status]}
      agentId={health.agent_id}
      detail={issues.length ? `Có vấn đề: ${issues.join(", ")}` : undefined}
    />
  );
}

export function AgentStatusFallback() {
  return <StatusLine dot="bg-line" label="Đang kiểm tra trạng thái" />;
}

function StatusLine({ dot, label, agentId, detail }: { dot: string; label: string; agentId?: string; detail?: string }) {
  return (
    <p className="flex items-center gap-2 text-sm" title={detail}>
      <span aria-hidden className={`size-2.5 rounded-full ${dot}`} />
      <span className="font-medium">{label}</span>
      {agentId && <span className="text-muted">{agentId}</span>}
      {detail && <span className="sr-only">{detail}</span>}
    </p>
  );
}
