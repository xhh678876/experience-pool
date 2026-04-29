# Experience Pool Review UI

Next.js 15 review UI for the standalone (SQLite) experience pool. The UI is
*also the backend*: server actions read and write the SQLite file at
`$EXP_DB_PATH` (default `~/.experience-pool/pool.db`) directly via
`better-sqlite3`. There is no separate API server.

## Run

```bash
cd ui
pnpm install   # or: npm install
pnpm dev       # or: npm run dev
# open http://localhost:3000
```

The first request will:

- open the SQLite database at `EXP_DB_PATH`
- create `pending_reembed` and `pending_rejudge` helper tables if they do not
  exist (the Python schema file is **not** modified)

To point at a different database:

```bash
EXP_DB_PATH=/path/to/pool.db pnpm dev
```

## Auth

There is no real SSO in this UI. Visit `/login`, type a name, and submit. That
sets the `X-Reviewer-Name` cookie. All reviewer actions append an `audit_log`
row with `actor=reviewer:<name>` (defaults to `anonymous` when unset).

## Pages

- `/` dashboard — totals, review backlog, q-distribution histogram, last-7-day
  ingest sparkline, top-10 reused experiences.
- `/experiences` list — sortable filter sidebar (review_status, task_type,
  sensitivity) and intent search.
- `/experiences/[id]` detail — four tabs:
  - **Card** — rendered intent + preconditions + script steps (numbered, with
    why/how) + tool capabilities + key decisions + pitfalls. Side panel shows
    the latest reward (5-dim breakdown + confidence + rationale + judge
    metadata), the current Q state, and a Q-update history derived from the
    `q_updates` table.
  - **Trajectory** — pretty-printed JSON read from `trajectory_path`. If a
    `<stem>.raw.json` sibling exists with different contents, a banner appears
    with a "show raw side-by-side" toggle.
  - **Lineage** — SVG graph: parents on the left, current node center,
    children on the right. Each child edge is dashed amber when
    `credit_applied=0` and solid green when `credit_applied=1`. Nodes are
    clickable.
  - **Audit** — rows from `audit_log` filtered to this `target_id`, newest
    first.
- Bottom action bar: Approve, Reject (with reason), Edit, Re-judge, Export
  JSON, Soft-delete.

## Server actions

In `app/_actions/actions.ts`. All actions write an `audit_log` entry stamped
with the reviewer.

| action      | effect |
|-------------|--------|
| `approve`   | sets `review_status='approved'` |
| `reject`    | sets `review_status='rejected'`, audit captures reason |
| `editCard`  | updates the editable card fields, sets `review_status='edited'`, resets `q_update_count=0`, enqueues a row in `pending_reembed` for the Python sidecar |
| `rejudge`   | enqueues a row in `pending_rejudge` |
| `softDelete`| sets `review_status='rejected'`, appends `soft_deleted` to `tags` |
| `exportJson`| redirects to `/api/export/[id]` which streams a JSON snapshot |

## Notes

- `better-sqlite3` is included as a server-external package so it isn't
  bundled into the client.
- All reads and writes happen in process. Lock contention with the Python
  pipeline is reduced by `journal_mode=WAL`. The reviewer never modifies the
  Python schema file under `core/`.
