export interface SkillRow {
  skill_id: string;
  agent_id: string;
  name: string;
  version: string;
  description: string | null;
  content_md: string | null;
  bundle_path: string | null;
  bundle_sha256: string | null;
  bundle_size_bytes: number;
  file_count: number;
  trigger_keywords: string;
  dependencies: string;
  frontmatter_json: string;
  q_outcome: number;
  q_intent: number;
  q_execution: number;
  q_orchestration: number;
  q_expression: number;
  q_update_count: number;
  invoke_count: number;
  install_count: number;
  sanitization_status: string;
  review_status: string;
  acl: string;
  tags: string;
  sensitivity: string;
  created_at: string;
}

export interface SkillUseRow {
  experience_id: string;
  skill_id: string;
  created_at: string;
  credit_applied: number;
  intent_text?: string | null;
  task_type?: string;
  name?: string;
  version?: string;
  description?: string | null;
}

export interface SkillQUpdateRow {
  update_id: string;
  skill_id: string;
  triggered_by_experience: string | null;
  alpha: number;
  confidence: number;
  delta_outcome: number | null;
  delta_intent: number | null;
  delta_execution: number | null;
  delta_orchestration: number | null;
  delta_expression: number | null;
  created_at: string;
}

export type ExperienceRow = {
  experience_id: string;
  agent_id: string;
  task_type: string;
  source_model: string;
  created_at: string;

  intent_text: string | null;
  preconditions: string | null;
  script_steps: string | null;
  tool_capabilities: string | null;
  key_decisions: string | null;
  pitfalls: string | null;

  summary: string | null;
  trajectory_path: string | null;
  tool_calls_count: number;
  token_count: number;
  duration_ms: number;

  q_outcome: number;
  q_intent: number;
  q_execution: number;
  q_orchestration: number;
  q_expression: number;
  q_update_count: number;

  visit_count: number;
  reuse_count: number;

  sanitization_status: string;
  review_status: string;
  extraction_status: string;
  acl: string;
  tags: string;
  sensitivity: string;
};

export type RewardRow = {
  reward_id: string;
  experience_id: string;
  judge_version: string;
  judge_model: string;
  r_outcome: number;
  r_intent: number;
  r_execution: number;
  r_orchestration: number;
  r_expression: number;
  confidence: number;
  self_consistency_variance: number | null;
  is_unstable: number;
  rationale: string | null;
  created_at: string;
};

export type EdgeRow = {
  parent_id: string;
  child_id: string;
  created_at: string;
  credit_applied: number;
};

export type QUpdateRow = {
  update_id: string;
  experience_id: string;
  triggered_by_child: string | null;
  alpha: number;
  confidence: number;
  delta_outcome: number | null;
  delta_intent: number | null;
  delta_execution: number | null;
  delta_orchestration: number | null;
  delta_expression: number | null;
  created_at: string;
};

export type AuditRow = {
  audit_id: number;
  actor: string;
  actor_kind: string;
  action: string;
  target_id: string | null;
  payload: string | null;
  created_at: string;
};

export type ExperienceListItem = ExperienceRow & {
  q_scalar: number;
};

export function qScalar(e: Pick<ExperienceRow, "q_outcome" | "q_intent" | "q_execution" | "q_orchestration" | "q_expression">): number {
  return (e.q_outcome + e.q_intent + e.q_execution + e.q_orchestration + e.q_expression) / 5;
}
