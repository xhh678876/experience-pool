"use client";

import { useActionState } from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { loginAction, type LoginFormState } from "./actions";

const initial: LoginFormState = { ok: false, message: "" };

export default function LoginForm({ nextPath }: { nextPath?: string }) {
  const [state, formAction, pending] = useActionState(loginAction, initial);
  return (
    <form action={formAction} className="space-y-3">
      <input type="hidden" name="next" value={nextPath ?? "/"} />
      <div>
        <label className="mb-1 block text-xs text-muted-foreground">邮箱</label>
        <Input
          name="email"
          type="email"
          placeholder="you@sii.edu.cn"
          autoComplete="email"
          required
          autoFocus
        />
      </div>
      <div>
        <label className="mb-1 block text-xs text-muted-foreground">密码</label>
        <Input
          name="password"
          type="password"
          autoComplete="current-password"
          required
        />
      </div>
      {state && !state.ok && state.message ? (
        <p className="rounded-md border border-rose-300 bg-rose-50 px-3 py-2 text-xs text-rose-800">
          {state.message}
        </p>
      ) : null}
      <div className="flex justify-end">
        <Button type="submit" disabled={pending}>
          {pending ? "登录中..." : "登录"}
        </Button>
      </div>
    </form>
  );
}
