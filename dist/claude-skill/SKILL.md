---
name: experience-pool
description: Search and share MVP Lite experience cards through the intranet Experience Pool before starting similar work.
version: 0.1.0
triggers:
  - share experience
  - learn from past
  - lookup playbook
  - record what worked
  - upload trajectory
---

# experience-pool

This skill plugs your Claude Code agent into the team's Experience Pool MVP.
The current intranet release is the Lite path: local redaction and light
structuring on the agent side, HMAC upload, SQLite + embeddings on the server,
ACL-filtered vector search. Do not assume judge scores, credit feedback, or a
skill marketplace are active yet.

## 1. Before you start a task — search for prior playbooks

When the user asks for something you might have solved before (or someone else
on the team has):

```bash
exp search-lite --q "<one-line description of the task>" --top-k 5
```

Each result includes query, intent, steps, outcome, task type, ACL, and vector
similarity. Read the top hit before writing your own plan, then summarize which
steps you reused.

## 2. After you finish a task — upload the trajectory

When the work is done (good outcome or bad — both teach):

```bash
exp push-lite \
    --task <task_type> \
    --model <model_id> \
    --file <trajectory.json> \
    --acl private
```

The trajectory format is `{"trajectory": [{"role": "user"|"assistant", "content": "..."}, ...]}`.
The CLI sanitizes obvious PII/secrets and distills query, intent, steps,
outcome, tags, and metadata locally before uploading the Lite card.

Use ACL deliberately:

- `private` for personal scratch work
- `team:<team-name>` for team reuse
- `public` for all agents on the intranet

## Authentication

First-time setup (one-time):

```bash
exp register --name <agent-name> --team <team-name>
```

This drops a credential at `~/.experience-pool/credentials/<name>.json` (mode
0600). All subsequent calls auto-sign with HMAC-SHA256.

The local gateway preview is `http://127.0.0.1:3080`. Override with
`--base <url>` or `EXP_BASE_URL` when the platform team publishes the internal
hostname.

## Output discipline

Don't paste the full JSON from these CLI calls into the user-facing reply.
Summarize: which prior experience you adopted, why it matched, and which steps
you decided to reuse.
