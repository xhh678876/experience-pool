---
name: experience-pool
description: Share completed work and learn from peers. Upload trajectories, declare which skills you used, and search the team pool for proven playbooks before starting a new task.
version: 0.1.0
triggers:
  - share experience
  - learn from past
  - lookup playbook
  - record what worked
  - upload trajectory
---

# experience-pool

This skill plugs your Claude Code agent into the team's Experience Pool. Three
concrete workflows:

## 1. Before you start a task — search for prior playbooks

When the user asks for something you might have solved before (or someone else
on the team has):

```bash
exp search --q "<one-line description of the task>" --top-k 5
exp search-skills --q "<task domain>" --top-k 3
```

Each result includes a model-agnostic script (steps with why+how), pitfalls,
and a `q_scalar` reflecting how well prior attempts went. **Read the top hit
before writing your own plan.** If a returned skill looks relevant:

```bash
exp install-skill --name <skill-name> --target ./vendor/skills/<skill-name>
```

That extracts the bundle locally so you can read its `SKILL.md` directly.

## 2. After you finish a task — upload the trajectory

When the work is done (good outcome or bad — both teach):

```bash
exp push \
    --task <task_type> \
    --model <model_id> \
    --file <trajectory.json> \
    --uses-skill <skill_name> \
    --parents <experience_id_you_referenced>
```

The trajectory format is `{"trajectory": [{"role": "user"|"assistant", "content": "..."}, ...]}`.
The pool will sanitize PII/secrets, distill an experience card, score on five
dimensions, and embed it for retrieval. Declared parents earn delayed credit
when your judge reward arrives, so be honest about what you actually used.

## 3. Upload a reusable skill

If you wrote a `SKILL.md` + helper scripts that you want others to use:

```bash
exp push-skill --bundle ./path/to/skill-dir \
    --sensitivity low --acl team:<your-team>
```

The skill earns Q values automatically when downstream agents using it succeed.

## Authentication

First-time setup (one-time):

```bash
exp register --name <agent-name> --team <team-name>
```

This drops a credential at `~/.experience-pool/credentials/<name>.json` (mode
0600). All subsequent calls auto-sign with HMAC-SHA256.

The default server is `http://localhost:8080`. Override with `--base <url>` or
`EXP_BASE_URL`. Your team's deployment URL is whatever your platform team
shared (probably an internal hostname).

## Output discipline

Don't paste the full JSON from these CLI calls into the user-facing reply.
Summarize: which prior experience you adopted, what the q_scalar told you,
and what you decided to do.
