"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { ADMIN_COOKIE } from "@/lib/admin";
import { ApiUnreachable, request } from "@/lib/api";

export type LoginState = { error: string | null };

function safeNext(value: FormDataEntryValue | null): string {
  const next = typeof value === "string" ? value : "";
  // Only paths on this site: "//evil.example" would leave it.
  return next.startsWith("/") && !next.startsWith("//") ? next : "/faults";
}

export async function login(_previous: LoginState, formData: FormData): Promise<LoginState> {
  let res: Response;
  try {
    res = await request("/api/v1/admin/login", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ password: String(formData.get("password") ?? "") }),
    });
  } catch (error) {
    if (error instanceof ApiUnreachable) return { error: "Không kết nối được agent-api. Kiểm tra docker compose." };
    throw error;
  }
  if (res.status === 401) return { error: "Sai mật khẩu. Mật khẩu là giá trị ADMIN_PASSWORD trong .env." };
  if (!res.ok) return { error: `agent-api trả lỗi HTTP ${res.status}.` };

  const { token, expires_in_s } = (await res.json()) as { token: string; expires_in_s: number };
  (await cookies()).set(ADMIN_COOKIE, token, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.COOKIE_SECURE === "true",
    maxAge: expires_in_s,
    path: "/",
  });
  redirect(safeNext(formData.get("next")));
}

export async function logout(): Promise<void> {
  (await cookies()).delete(ADMIN_COOKIE);
  redirect("/login");
}
