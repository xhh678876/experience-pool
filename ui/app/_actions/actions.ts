"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { getDb } from "@/lib/db";
import { actorString, getReviewerName } from "@/lib/auth";

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
}

export async function rejudgeAction(formData: FormData): Promise<void> {
  const id = String(formData.get("id") ?? "");
  if (!id) return;
  ensureExists(id);
  const reviewer = await getReviewerName();
  const actor = actorString(reviewer);
  const db = getDb();
  db.prepare(
    "INSERT INTO pending_rejudge (experience_id) VALUES (?)",
  ).run(id);
  appendAudit(actor, "rejudge_requested", id, { reviewer });
  revalidatePath(`/experiences/${id}`);
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
  db.prepare(
    "INSERT INTO pending_reembed (experience_id) VALUES (?)",
  ).run(id);
  appendAudit(actor, "edit_card", id, {
    reviewer,
    fields: updates.map((u) => u.col),
  });
  revalidatePath(`/experiences/${id}`);
  revalidatePath("/experiences");
  revalidatePath("/");
}

export async function exportJsonAction(formData: FormData): Promise<void> {
  const id = String(formData.get("id") ?? "");
  if (!id) return;
  // Server actions cannot return a file response directly here, so we redirect
  // to a streaming route that produces the export.
  redirect(`/api/export/${encodeURIComponent(id)}`);
}
