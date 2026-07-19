# Runtime configuration

`config/env.sh` is the single entry point for paths, ports, and sibling
repository locations. Source it from shell scripts and CI jobs:

```bash
EXP_ENV=development source config/env.sh
bash scripts/run-intranet-local.sh
```

Production hosts use the same loader with an ignored machine-local profile:

```bash
cp config/environments/production.local.sh.example \
  config/environments/production.local.sh
$EDITOR config/environments/production.local.sh
EXP_ENV=production bash scripts/babysit.sh
```

Precedence is:

1. Variables already exported by the caller or CI.
2. `config/environments/<profile>.local.sh` (git-ignored).
3. Tracked `development.sh` or `production.sh` defaults.

The loader keeps these locations aligned:

- `EXP_ROOT`, `EXP_DB_PATH`, `EXP_TRAJECTORIES_DIR`, `EXP_CREDENTIALS_DIR`
- `EXP_REPO_ROOT`, `EXP_WORKSPACE_ROOT`, `EXP_PLUGIN_REPO`, `EXP_FLEET_REPO`
- API, UI, and gateway ports/origins
- runtime, maintenance, and service log paths

Run `EXP_ENV=production bash config/env.sh` to inspect the effective non-secret
configuration. Secrets remain in the generated `EXP_RUNTIME_ENV` file or the
host's secret manager; they do not belong in these profiles.

Sibling plugin release scripts discover this file automatically when both repos
share one parent directory. Independent checkouts can point to it explicitly:

```bash
EXPOOL_CONFIG_FILE=/srv/src/experience-pool/config/env.sh \
EXP_ENV=production npm run release:check
```
