"use client";

import { useActionState } from "react";

import { login, type LoginState } from "./actions";

export function LoginForm({ next }: { next: string }) {
  const [state, action, pending] = useActionState<LoginState, FormData>(login, { error: null });
  return (
    <form action={action} className="flex max-w-sm flex-col gap-3">
      <h1 className="text-2xl font-bold tracking-tight">Đăng nhập quản trị</h1>
      <p className="text-sm text-muted">Cần đăng nhập để bật lỗi giả lập và gửi message thử tới agent khác.</p>
      <input type="hidden" name="next" value={next} />
      <label htmlFor="password" className="mt-2 text-sm font-medium">
        Mật khẩu
      </label>
      <input
        id="password"
        name="password"
        type="password"
        required
        autoComplete="current-password"
        className="rounded-md border border-line bg-surface px-3 py-2.5"
      />
      {state.error && (
        <p role="alert" className="text-sm text-warn">
          {state.error}
        </p>
      )}
      <button
        type="submit"
        disabled={pending}
        className="rounded-md bg-action px-5 py-2.5 font-medium text-white hover:bg-action-strong disabled:opacity-60"
      >
        {pending ? "Đang đăng nhập" : "Đăng nhập"}
      </button>
    </form>
  );
}
