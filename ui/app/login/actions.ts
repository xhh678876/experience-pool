"use server";

import { redirect } from "next/navigation";
import { apiLogin, apiLogout } from "@/lib/users-api";
import { withPublicBase } from "@/lib/base-path";

export interface LoginFormState {
  ok: boolean;
  message: string;
}

function safeNext(next: string | null): string {
  // Only honor same-origin relative paths; anything else falls back to /.
  if (!next) return "/";
  if (!next.startsWith("/")) return "/";
  if (next.startsWith("//")) return "/";
  return next;
}

export async function loginAction(
  _prev: LoginFormState | undefined,
  formData: FormData,
): Promise<LoginFormState> {
  const email = String(formData.get("email") || "").trim().toLowerCase();
  const password = String(formData.get("password") || "");
  const nextPath = safeNext(String(formData.get("next") || ""));
  if (!email || !password) {
    return { ok: false, message: "邮箱和密码必填" };
  }
  const result = await apiLogin({ email, password });
  if (!result.ok) {
    return { ok: false, message: result.message ?? "登录失败" };
  }
  redirect(withPublicBase(nextPath));
}

export async function logoutAction(): Promise<void> {
  await apiLogout();
  redirect(withPublicBase("/login"));
}
