"use server";

import { revalidatePath } from "next/cache";
import {
  apiCreateApiKey,
  apiCreatePairingCode,
  apiRevokeApiKey,
  type ApiKeyCreateResult,
  type PairingCodeCreateResult,
} from "@/lib/users-api";

export interface CreateKeyFormState {
  ok: boolean;
  status: number;
  message?: string;
  // Set only on success — contains the *raw* api_key, shown once.
  created?: ApiKeyCreateResult;
}

export async function createApiKeyAction(
  _prev: CreateKeyFormState | undefined,
  formData: FormData,
): Promise<CreateKeyFormState> {
  const name = String(formData.get("name") ?? "").trim();
  if (!name) {
    return { ok: false, status: 400, message: "请填写名称" };
  }
  const r = await apiCreateApiKey(name);
  revalidatePath("/me/api-keys");
  if (!r.ok || !r.data) {
    return { ok: false, status: r.status, message: r.message ?? "创建失败" };
  }
  return { ok: true, status: r.status, created: r.data };
}

export interface CreatePairingCodeFormState {
  ok: boolean;
  status: number;
  message?: string;
  created?: PairingCodeCreateResult;
}

export async function createPairingCodeAction(
  _prev: CreatePairingCodeFormState | undefined,
  formData: FormData,
): Promise<CreatePairingCodeFormState> {
  const name = String(formData.get("name") ?? "").trim() || "plugin-pairing";
  const ttlRaw = Number(formData.get("ttl_seconds") ?? 600);
  const ttlSeconds = Number.isFinite(ttlRaw) ? ttlRaw : 600;
  const r = await apiCreatePairingCode(name, ttlSeconds);
  revalidatePath("/me/api-keys");
  if (!r.ok || !r.data) {
    return { ok: false, status: r.status, message: r.message ?? "创建失败" };
  }
  return { ok: true, status: r.status, created: r.data };
}

export interface RevokeKeyFormState {
  ok: boolean;
  status: number;
  message?: string;
}

export async function revokeApiKeyAction(
  _prev: RevokeKeyFormState | undefined,
  formData: FormData,
): Promise<RevokeKeyFormState> {
  const keyId = String(formData.get("key_id") ?? "").trim();
  if (!keyId) {
    return { ok: false, status: 400, message: "missing key_id" };
  }
  const r = await apiRevokeApiKey(keyId);
  revalidatePath("/me/api-keys");
  return { ok: r.ok, status: r.status, message: r.message };
}
