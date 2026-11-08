import type { Metadata } from "next";
import { Suspense } from "react";

import { LoginForm } from "./login-form";

export const metadata: Metadata = { title: "Đăng nhập" };

export default function LoginPage({ searchParams }: PageProps<"/login">) {
  return (
    <Suspense fallback={<LoginForm next="/faults" />}>
      <WithNext searchParams={searchParams as Promise<{ next?: string }>} />
    </Suspense>
  );
}

async function WithNext({ searchParams }: { searchParams: Promise<{ next?: string }> }) {
  const { next } = await searchParams;
  return <LoginForm next={typeof next === "string" ? next : "/faults"} />;
}
