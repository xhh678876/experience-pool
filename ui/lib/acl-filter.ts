/**
 * ACL filter helper. Used by every "global browse" query so private
 * content from other users is NEVER visible to the current viewer.
 *
 * Rule:
 *   - row.acl = 'public' (or 'org' legacy alias) → visible to everyone
 *   - row.agent.owner = current_user.email      → visible (you own it)
 *   - row.agent.name  = current_user.default_agent_name → visible
 *   - else → hidden
 *
 * Pages that need to see ALL of a user's own content (incl. private)
 * should use the personal queries in `me-queries.ts`, NOT call into
 * the global listings above.
 */

import { getCurrentUser } from "./auth";

export interface AclFragment {
  /** SQL fragment (no leading WHERE/AND) */
  sql: string;
  /** parameters to bind into the prepared statement, in order */
  params: unknown[];
}

/**
 * Build an ACL visibility fragment that **assumes a JOIN against agents
 * table aliased as `a` and the experiences row aliased as `e`**.
 *
 * Strict mode by design: GLOBAL browse pages only show
 * `acl IN ('public', 'org')`. Even the owner of a private row does NOT
 * see it through these queries — they must go to `/me` (which uses
 * `me-queries.ts`, NOT this filter) to see their own private content.
 *
 * Rationale: users uploading to their private repo expect those rows to
 * stay personal. Showing them in `/experiences` or the home "recent
 * sessions" list (even just to themselves) felt like a leak.
 */
export async function aclVisibilityClause(): Promise<AclFragment> {
  // No params, no per-user context — global pages are user-agnostic and
  // only ever surface `public`. Keeping the function async so existing
  // call sites don't need to change shape.
  return {
    sql: "COALESCE(e.acl, 'private') IN ('public', 'org')",
    params: [],
  };
}

/** Sync variant — only ever returns the public-only filter now.
 * Kept as separate export so callers that may need to whitelist a
 * specific agent later don't have to be re-imported. */
export function aclVisibilityClauseFor(
  _viewerEmail: string | null,
  _viewerAgentName: string | null,
): AclFragment {
  return {
    sql: "COALESCE(e.acl, 'private') IN ('public', 'org')",
    params: [],
  };
}

/**
 * Detail-page variant: public OR the owner themselves. Used by routes
 * that show a SINGLE row that the user navigated to (e.g. clicking a
 * card on /me, where they own the private row). Without this, owners
 * 404 their own private content the moment they click into it.
 */
export async function aclVisibilityForDetail(): Promise<AclFragment> {
  const me = await getCurrentUser();
  const conds = ["COALESCE(e.acl, 'private') IN ('public', 'org')"];
  const params: unknown[] = [];
  if (me) {
    conds.push("a.owner = ?");
    params.push(me.email);
    conds.push("a.name = ?");
    params.push(me.default_agent_name);
  }
  return {
    sql: `(${conds.join(" OR ")})`,
    params,
  };
}
