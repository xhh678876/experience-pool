"use server";

import { revalidatePath } from "next/cache";
import { revokeExperience } from "@/lib/me-queries";
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
