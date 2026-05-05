"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { getDb } from "@/lib/db";
import { actorString, getReviewerName } from "@/lib/auth";
import { withBase } from "@/components/ui/link";

type NoticeKind = "success" | "warn" | "danger";

function actionReturnTo(id: string, formData: FormData): string {
  const raw = String(formData.get("returnTo") ?? "").trim();
  const fallback = `/experiences/${encodeURIComponent(id)}`;
  if (!raw.startsWith(`/experiences/${id}`)) return fallback;
  try {
    const url = new URL(raw, "http://experience-pool.local");
    url.searchParams.delete("notice");
    url.searchParams.delete("noticeKind");
    return `${url.pathname}${url.search}`;
  } catch {
    return fallback;
  }
}

function redirectWithNotice(
  id: string,
  formData: FormData,
  notice: string,
  noticeKind: NoticeKind = "success",
): never {
  const url = new URL(actionReturnTo(id, formData), "http://experience-pool.local");
  url.searchParams.set("notice", notice);
  url.searchParams.set("noticeKind", noticeKind);
  redirect(withBase(`${url.pathname}${url.search}`));
}

function appendAudit(
  actor: string,
  action: string,
  targetId: string,
  payload: unknown,
): void {
  const db = getDb();
  db.prepare(
    `INSERT INTO audit_log (actor, actor_kind, action, target_id, payload)
     VALUES (?, ?, ?, ?, ?)`,
  ).run(actor, "human", action, targetId, JSON.stringify(payload ?? {}));
}

function ensureExists(id: string): void {
  const db = getDb();
  const row = db
    .prepare("SELECT 1 FROM experiences WHERE experience_id = ?")
    .get(id);
  if (!row) {
    throw new Error(`experience ${id} not found`);
  }
}

export async function approveAction(formData: FormData): Promise<void> {
  const id = String(formData.get("id") ?? "");
  if (!id) return;
  ensureExists(id);
  const reviewer = await getReviewerName();
  const actor = actorString(reviewer);
  const db = getDb();
  db.prepare(
    "UPDATE experiences SET review_status = 'approved' WHERE experience_id = ?",
  ).run(id);
  appendAudit(actor, "approve", id, { reviewer });
  revalidatePath(`/experiences/${id}`);
  revalidatePath("/experiences");
  revalidatePath("/");
  redirectWithNotice(id, formData, "已通过并写入审计日志");
}

export async function rejectAction(formData: FormData): Promise<void> {
  const id = String(formData.get("id") ?? "");
  const reason = String(formData.get("reason") ?? "").trim();
  if (!id) return;
  ensureExists(id);
  const reviewer = await getReviewerName();
  const actor = actorString(reviewer);
  const db = getDb();
  db.prepare(
    "UPDATE experiences SET review_status = 'rejected' WHERE experience_id = ?",
  ).run(id);
  appendAudit(actor, "reject", id, { reviewer, reason });
  revalidatePath(`/experiences/${id}`);
  revalidatePath("/experiences");
  revalidatePath("/");
  redirectWithNotice(id, formData, "已拒绝并记录原因", "warn");
}

export async function softDeleteAction(formData: FormData): Promise<void> {
  const id = String(formData.get("id") ?? "");
  if (!id) return;
  ensureExists(id);
  const reviewer = await getReviewerName();
  const actor = actorString(reviewer);
  const db = getDb();
  // Tag soft_deleted by appending to the JSON tags array in-place.
  const row = db
    .prepare("SELECT tags FROM experiences WHERE experience_id = ?")
    .get(id) as { tags: string | null };
  let tags: string[] = [];
  try {
    tags = row?.tags ? JSON.parse(row.tags) : [];
    if (!Array.isArray(tags)) tags = [];
  } catch {
    tags = [];
  }
  if (!tags.includes("soft_deleted")) tags.push("soft_deleted");
  db.prepare(
    "UPDATE experiences SET review_status = 'rejected', tags = ? WHERE experience_id = ?",
  ).run(JSON.stringify(tags), id);
  appendAudit(actor, "soft_delete", id, { reviewer });
  revalidatePath(`/experiences/${id}`);
  revalidatePath("/experiences");
  revalidatePath("/");
  redirectWithNotice(id, formData, "已标记为 soft_deleted", "warn");
}

export async function rejudgeAction(formData: FormData): Promise<void> {
  const id = String(formData.get("id") ?? "");
  if (!id) return;
  ensureExists(id);
  const reviewer = await getReviewerName();
  const actor = actorString(reviewer);
  const db = getDb();
  const queued = db
    .prepare(
      "SELECT 1 FROM pending_rejudge WHERE experience_id = ? AND processed = 0 LIMIT 1",
    )
    .get(id);
  if (!queued) {
    db.prepare(
      "INSERT INTO pending_rejudge (experience_id) VALUES (?)",
    ).run(id);
  }
  appendAudit(actor, "rejudge_requested", id, { reviewer, alreadyQueued: Boolean(queued) });
  revalidatePath(`/experiences/${id}`);
  redirectWithNotice(id, formData, queued ? "这条经验已在重判队列中" : "已加入重判队列");
}

const EDITABLE_FIELDS = [
  "intent_text",
  "preconditions",
  "script_steps",
  "tool_capabilities",
  "key_decisions",
  "pitfalls",
] as const;

export async function editCardAction(formData: FormData): Promise<void> {
  const id = String(formData.get("id") ?? "");
  if (!id) return;
  ensureExists(id);
  const reviewer = await getReviewerName();
  const actor = actorString(reviewer);
  const db = getDb();
  const updates: { col: string; value: string }[] = [];
  for (const field of EDITABLE_FIELDS) {
    const raw = formData.get(field);
    if (raw == null) continue;
    updates.push({ col: field, value: String(raw) });
  }
  if (updates.length > 0) {
    const setClause = updates.map((u) => `${u.col} = ?`).join(", ");
    const params: unknown[] = updates.map((u) => u.value);
    params.push(id);
    db.prepare(
      `UPDATE experiences SET ${setClause}, q_update_count = 0, review_status = 'edited' WHERE experience_id = ?`,
    ).run(...params);
  }
  // Queue the experience for re-embedding by the Python sidecar.
  const queued = db
    .prepare(
      "SELECT 1 FROM pending_reembed WHERE experience_id = ? AND processed = 0 LIMIT 1",
    )
    .get(id);
  if (!queued) {
    db.prepare(
      "INSERT INTO pending_reembed (experience_id) VALUES (?)",
    ).run(id);
  }
  appendAudit(actor, "edit_card", id, {
    reviewer,
    fields: updates.map((u) => u.col),
    alreadyQueued: Boolean(queued),
  });
  revalidatePath(`/experiences/${id}`);
  revalidatePath("/experiences");
  revalidatePath("/");
  redirectWithNotice(
    id,
    formData,
    queued ? "编辑已保存，这条经验已在重新 embedding 队列中" : "编辑已保存，已加入重新 embedding 队列",
  );
}

export async function exportJsonAction(formData: FormData): Promise<void> {
  const id = String(formData.get("id") ?? "");
  if (!id) return;
  // Server actions cannot return a file response directly here, so we redirect
  // to a streaming route that produces the export.
  redirect(withBase(`/api/export/${encodeURIComponent(id)}`));
}
