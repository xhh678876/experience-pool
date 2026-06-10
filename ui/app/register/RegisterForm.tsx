"use client";

import { useActionState } from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { registerAction, type RegisterFormState } from "./actions";

const initial: RegisterFormState = { ok: false, message: "" };

export default function RegisterForm() {
  const [state, formAction, pending] = useActionState(registerAction, initial);

  return (
    <form action={formAction} className="space-y-3">
      <div>
        <label className="mb-1 block text-xs text-muted-foreground">邮箱</label>
        <Input
          name="email"
          type="email"
          placeholder="you@example.com"
          autoComplete="email"
          required
          autoFocus
        />
      </div>
      <div>
        <label className="mb-1 block text-xs text-muted-foreground">
          密码(至少 8 位)
        </label>
        <Input
          name="password"
          type="password"
          autoComplete="new-password"
          minLength={8}
          required
        />
      </div>
      <div>
        <label className="mb-1 block text-xs text-muted-foreground">
          再次输入密码
        </label>
        <Input
          name="confirm"
          type="password"
          autoComplete="new-password"
          minLength={8}
          required
        />
      </div>
      <div>
        <label className="mb-1 block text-xs text-muted-foreground">
          显示名(可选)
        </label>
        <Input name="display_name" placeholder="张三" />
      </div>
      {state && !state.ok && state.message ? (
        <p className="rounded-md border border-rose-300 bg-rose-50 px-3 py-2 text-xs text-rose-800">
          {state.message}
        </p>
      ) : null}
      <div className="flex justify-end">
        <Button type="submit" disabled={pending}>
          {pending ? "注册中..." : "注册"}
        </Button>
      </div>
    </form>
  );
}
