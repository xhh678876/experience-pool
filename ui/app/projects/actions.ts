"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import {
  apiAcceptProjectInvite,
  apiCreateProject,
  apiCreateProjectInvite,
  apiGrantMyPool,
  apiRevokeProjectGrant,
  type ProjectInvite,
} from "@/lib/projects-api";

export async function createProjectAction(formData: FormData): Promise<void> {
  const name = String(formData.get("name") ?? "").trim();
  const slug = String(formData.get("slug") ?? "").trim();
  if (!name) return;
  const res = await apiCreateProject({ name, slug: slug || undefined });
  if (res.ok && res.data) {
    revalidatePath("/projects");
    redirect(`/projects/${res.data.slug}`);
  }
}

export async function acceptProjectInviteAction(formData: FormData): Promise<void> {
  const token = String(formData.get("token") ?? "").trim();
  if (!token) return;
  const res = await apiAcceptProjectInvite(token);
  if (res.ok && res.data) {
    revalidatePath("/projects");
    redirect(`/projects/${res.data.project_slug}`);
  }
}

export type InviteFormState = {
  ok: boolean;
  status: number;
  message?: string;
  invite?: ProjectInvite;
};

export async function createProjectInviteAction(
  _prev: InviteFormState | undefined,
  formData: FormData,
): Promise<InviteFormState> {
  const slug = String(formData.get("project") ?? "").trim();
  const email = String(formData.get("email") ?? "").trim();
  const role = String(formData.get("role") ?? "member").trim() || "member";
  if (!slug || !email) {
    return { ok: false, status: 400, message: "请填写邮箱" };
  }
  const res = await apiCreateProjectInvite(slug, { email, role });
  revalidatePath(`/projects/${slug}`);
  if (!res.ok || !res.data) {
    return { ok: false, status: res.status, message: res.message ?? "创建邀请失败" };
  }
  return { ok: true, status: res.status, invite: res.data };
}

export async function grantMyPoolAction(formData: FormData): Promise<void> {
  const slug = String(formData.get("project") ?? "").trim();
  const includeHigh = String(formData.get("include_high") ?? "") === "on";
  if (!slug) return;
  await apiGrantMyPool(slug, includeHigh);
  revalidatePath(`/projects/${slug}`);
}

export async function revokeProjectGrantAction(formData: FormData): Promise<void> {
  const slug = String(formData.get("project") ?? "").trim();
  const owner = String(formData.get("owner") ?? "").trim();
  if (!slug || !owner) return;
  await apiRevokeProjectGrant(slug, owner);
  revalidatePath(`/projects/${slug}`);
}
