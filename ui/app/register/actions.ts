"use server";

import { redirect } from "next/navigation";
import { apiRegister } from "@/lib/users-api";
import { withBase } from "@/components/ui/link";

export interface RegisterFormState {
  ok: boolean;
  message: string;
  bindCommand?: string;
  agentName?: string;
}

export async function registerAction(
  _prev: RegisterFormState | undefined,
  formData: FormData,
): Promise<RegisterFormState> {
  const email = String(formData.get("email") || "").trim().toLowerCase();
  const password = String(formData.get("password") || "");
  const confirm = String(formData.get("confirm") || "");
  const displayName = String(formData.get("display_name") || "").trim();

  if (!email || !password) {
    return { ok: false, message: "邮箱和密码必填" };
  }
  if (password.length < 8) {
    return { ok: false, message: "密码至少 8 位" };
  }
  if (password !== confirm) {
    return { ok: false, message: "两次输入的密码不一致" };
  }

  const result = await apiRegister({
    email,
    password,
    display_name: displayName || undefined,
  });

  if (!result.ok) {
    return { ok: false, message: result.message ?? "注册失败" };
  }

  // Auto-logged-in via cookie. Bounce to home where the bind script is shown.
  redirect(withBase("/"));
}
