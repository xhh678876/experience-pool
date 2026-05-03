"use server";

import { revalidatePath } from "next/cache";
import { revokeExperience } from "@/lib/me-queries";
import {
  publishExperience,
  unpublishExperience,
  type PublishHit,
} from "@/lib/publish-queries";
import { getReviewerName } from "@/lib/auth";

interface RevokeFormState {
  ok: boolean;
  status: string;
  message: string;
  experience_id: string;
  deleted_files: string[];
}

export async function revokeAction(
  prevState: RevokeFormState | undefined,
  formData: FormData
): Promise<RevokeFormState> {
  const eid = String(formData.get("eid") || "").trim();
  const reason = String(formData.get("reason") || "user_clicked_revoke").trim();
  if (!eid) {
    return {
      ok: false,
      status: "invalid",
      message: "missing experience_id",
      experience_id: "",
      deleted_files: [],
    };
  }
  const viewer = await getReviewerName();
  const result = await revokeExperience(viewer, eid, reason);
  revalidatePath("/me");
  revalidatePath("/experiences");
  revalidatePath("/sessions");
  return {
    ok: result.ok,
    status: result.status,
    message: result.error || result.status,
    experience_id: result.experience_id,
    deleted_files: result.deleted_files,
  };
}

export interface PublishFormState {
  ok: boolean;
  status: string;
  message: string;
  experience_id: string;
  blocking_hits: PublishHit[];
  publish_count?: number;
  community_unlocked?: boolean;
}

export async function publishAction(
  prevState: PublishFormState | undefined,
  formData: FormData
): Promise<PublishFormState> {
  const eid = String(formData.get("eid") || "").trim();
  if (!eid) {
    return {
      ok: false,
      status: "invalid",
      message: "missing experience_id",
      experience_id: "",
      blocking_hits: [],
    };
  }
  const viewer = await getReviewerName();
  const result = await publishExperience(viewer, eid);
  revalidatePath("/me");
  revalidatePath("/community");
  return {
    ok: result.ok,
    status: result.status,
    message:
      result.error ??
      (result.status === "blocked"
        ? `严格脱敏拦截了 ${result.blocking_hits.length} 处敏感内容`
        : result.status),
    experience_id: result.experience_id,
    blocking_hits: result.blocking_hits,
    publish_count: result.publish_count,
    community_unlocked: result.community_unlocked,
  };
}

export async function unpublishAction(
  prevState: PublishFormState | undefined,
  formData: FormData
): Promise<PublishFormState> {
  const eid = String(formData.get("eid") || "").trim();
  if (!eid) {
    return {
      ok: false,
      status: "invalid",
      message: "missing experience_id",
      experience_id: "",
      blocking_hits: [],
    };
  }
  const viewer = await getReviewerName();
  const result = await unpublishExperience(viewer, eid);
  revalidatePath("/me");
  revalidatePath("/community");
  return {
    ok: result.ok,
    status: result.status,
    message: result.error ?? result.status,
    experience_id: result.experience_id,
    blocking_hits: [],
    publish_count: result.publish_count,
    community_unlocked: result.community_unlocked,
  };
}
