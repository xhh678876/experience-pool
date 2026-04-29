import { cookies } from "next/headers";

export async function getReviewerName(): Promise<string> {
  const c = await cookies();
  const v = c.get("X-Reviewer-Name")?.value;
  if (!v || v.trim().length === 0) return "anonymous";
  return v.trim();
}

export function actorString(reviewer: string): string {
  return `reviewer:${reviewer}`;
}
