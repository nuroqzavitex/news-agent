import "server-only";

import { cookies } from "next/headers";

import { request } from "./api";

export const ADMIN_COOKIE = "admin_token";

/** The session ended or never started: the page should send the person to /login. */
export class AdminUnauthorized extends Error {}

export async function adminToken(): Promise<string | null> {
  return (await cookies()).get(ADMIN_COOKIE)?.value ?? null;
}

/** A request to /api/v1/admin/* with the session token from the cookie. */
export async function adminRequest(path: string, init: RequestInit = {}): Promise<Response> {
  const token = await adminToken();
  if (!token) throw new AdminUnauthorized();
  const res = await request(path, {
    ...init,
    headers: { ...init.headers, authorization: `Bearer ${token}`, "content-type": "application/json" },
  });
  if (res.status === 401) throw new AdminUnauthorized();
  return res;
}

export async function adminJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await adminRequest(path, init);
  if (!res.ok) throw new Error(`${init.method ?? "GET"} ${path} failed with HTTP ${res.status}: ${await res.text()}`);
  return res.json() as Promise<T>;
}
