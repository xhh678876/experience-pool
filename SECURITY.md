# Security Policy

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security problems.

Report privately to **xiehaohui766@gmail.com** with:

- a description of the issue and its impact,
- steps to reproduce (a proof of concept if you have one),
- the affected version / commit.

You can expect an acknowledgement within a few days. Once a fix is
available we will credit reporters who want to be named.

## Supported versions

This is an early-stage project; only the `main` branch receives security
fixes.

## Hardening notes for self-hosters

This service is designed to be self-hosted. A few defaults favor
out-of-the-box usability over lockdown — review them before exposing the
service to an untrusted network:

- **Registration is open by default.** `POST /v1/agents/register` and
  `POST /v1/users/register` accept anyone unless you set
  `EXP_REGISTER_TOKEN` / `EXP_USER_REGISTER_TOKEN`. The server logs a
  warning at startup while these are unset. See `.env.example`.
- **Admin endpoints are fail-closed.** `/v1/admin/*` stay disabled until
  `EXP_ADMIN_TOKEN` is set.
- **Re-registering an existing agent name is refused** (HTTP 409) unless a
  valid `X-Register-Token` is presented, so an existing agent's credential
  cannot be silently rotated by an anonymous caller.
- **Run behind TLS** and set `EXP_SESSION_COOKIE_SECURE=1` so session
  cookies are only sent over HTTPS.
- **Set a stable `EXP_SESSION_SECRET`** in multi-host deployments.

The `gateway/` directory contains an earlier, unauthenticated prototype of
the API and is **not** part of the supported deployment path (the deployed
server is `core/exp_core/server.py`). Do not expose `gateway/` publicly.
