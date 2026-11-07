import "server-only";

import type { components } from "./api-types";

type Schemas = components["schemas"];
export type SearchResult = Schemas["SearchResult"];
export type SearchHit = Schemas["SearchHit"];
export type ArticleDetail = Schemas["ArticleDetail"];
export type SourceOut = Schemas["SourceOut"];
export type CrawlGrid = Schemas["CrawlGrid"];

export type Health = {
  status: "online" | "degraded" | "offline";
  agent_id: string;
  uptime_s: number;
  freshness: string | null;
  deps: Record<string, string>;
  modes: Record<string, string>;
};

/** Server-side base URL of agent-api. Inside Docker this is the service name, not localhost. */
export const API_BASE = process.env.API_INTERNAL_URL ?? "http://localhost:8008";

/** agent-api could not be reached at all (container down, wrong URL). */
export class ApiUnreachable extends Error {
  constructor() {
    super(`agent-api is unreachable at ${API_BASE}`);
  }
}

type Query = Record<string, string | number | string[] | undefined>;

function buildUrl(path: string, query?: Query): URL {
  const url = new URL(path, API_BASE);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value === undefined || value === "") continue;
    for (const v of Array.isArray(value) ? value : [value]) url.searchParams.append(key, String(v));
  }
  return url;
}

async function request(path: string, init?: RequestInit & { query?: Query }): Promise<Response> {
  try {
    return await fetch(buildUrl(path, init?.query), init);
  } catch {
    throw new ApiUnreachable();
  }
}

async function getJson<T>(path: string, query?: Query): Promise<T> {
  const res = await request(path, { query });
  if (!res.ok) throw new Error(`GET ${path} failed with HTTP ${res.status}`);
  return res.json() as Promise<T>;
}

export const api = {
  health: async (): Promise<Health> => {
    // /health answers 503 with a body when the agent is offline; that is still a valid health report.
    const res = await request("/health");
    return res.json() as Promise<Health>;
  },
  articles: (query: {
    q?: string;
    source?: string;
    sentiment?: string;
    mode?: string;
    page?: number;
    page_size?: number;
  }) =>
    getJson<SearchResult>("/api/v1/articles", query),
  article: async (id: string): Promise<ArticleDetail | null> => {
    const res = await request(`/api/v1/articles/${encodeURIComponent(id)}`);
    if (res.status === 404) return null;
    if (!res.ok) throw new Error(`GET article ${id} failed with HTTP ${res.status}`);
    return res.json() as Promise<ArticleDetail>;
  },
  sources: () => getJson<SourceOut[]>("/api/v1/sources"),
  crawlGrid: (hours = 24) => getJson<CrawlGrid>("/api/v1/stats/crawl-grid", { hours }),
  triggerCrawl: async (sourceCode?: string): Promise<string[]> => {
    const res = await request("/api/v1/crawl/trigger", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ source_code: sourceCode ?? null }),
    });
    if (!res.ok) throw new Error(`crawl trigger failed with HTTP ${res.status}`);
    return ((await res.json()) as { enqueued: string[] }).enqueued;
  },
};
