import { NextResponse } from "next/server";
import {
  getAuditsForTarget,
  getChildren,
  getExperience,
  getLatestReward,
  getParents,
  getQUpdates,
} from "@/lib/queries";

export const dynamic = "force-dynamic";

export async function GET(
  _req: Request,
  ctx: { params: Promise<{ id: string }> },
) {
  const { id } = await ctx.params;
  const exp = getExperience(id);
  if (!exp) {
    return NextResponse.json({ error: "not_found" }, { status: 404 });
  }
  const payload = {
    experience: exp,
    reward: getLatestReward(id),
    q_updates: getQUpdates(id),
    parents: getParents(id),
    children: getChildren(id),
    audits: getAuditsForTarget(id),
  };
  return new NextResponse(JSON.stringify(payload, null, 2), {
    status: 200,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "content-disposition": `attachment; filename="experience-${id}.json"`,
    },
  });
}
