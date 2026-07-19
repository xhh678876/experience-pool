#!/usr/bin/env node
/**
 * Experience Pool CLI — TypeScript, npm-distributed.
 *
 *   exp register --name <agent> --team <team> [--base <url>]
 *   exp whoami
 *   exp push --task <type> --model <m> --file traj.json [--uses-skill foo --parents id1,id2]
 *   exp search --q "..." [--task t] [--top-k 5]
 *   exp push-skill --bundle ./my-skill [--sensitivity low|medium|high]
 *   exp search-skills --q "..."
 *   exp install-skill --name <skill> --target ./vendor/skills/<skill>
 *   exp dashboard
 *   exp leaderboard
 *
 * Credentials live in ~/.experience-pool/credentials/<agent>.json (mode 0600).
 * Override server with --base or EXP_BASE_URL.
 */

import { Command } from "commander";
import fs from "node:fs";
import path from "node:path";
import tar from "node:tty"; // unused placeholder, will inline tar via child_process below

import {
  DEFAULT_BASE_URL,
  loadCredential,
  saveCredential,
  type Credential,
} from "./config.js";
import { GatewayClient } from "./client.js";
import { prepareLocalRuleBased } from "./lite.js";

void tar; // keep ts happy if reformat trims this

const program = new Command();
program
  .name("exp")
  .description("Experience Pool client (npm-distributed CLI)")
  .version("0.1.0")
  .option(
    "--base <url>",
    "Gateway URL (env EXP_BASE_URL, default https://expool.clawsii.com)",
    DEFAULT_BASE_URL,
  );

function buildClient(cmd: Command, requireAuth: boolean): GatewayClient {
  const base = (cmd.parent?.opts().base as string) ?? DEFAULT_BASE_URL;
  const cred = loadCredential();
  if (requireAuth && cred === null) {
    console.error(
      "no credential found. run `exp register` first or set EXP_AGENT_NAME / EXP_AGENT_SECRET.",
    );
    process.exit(1);
  }
  return new GatewayClient({ baseUrl: base, cred });
}

program
  .command("register")
  .description("Register an agent and download an HMAC credential")
  .requiredOption("--name <name>", "Agent name (slug)")
  .requiredOption("--team <team>", "Team this agent belongs to")
  .action(async (opts, cmd) => {
    const client = buildClient(cmd, false);
    const cred = await client.request<Credential>("POST", "/v1/agents/register", {
      name: opts.name,
      team: opts.team,
    });
    const file = saveCredential(cred);
    console.log(JSON.stringify({ ...cred, credentials_path: file }, null, 2));
  });

program
  .command("whoami")
  .description("Print the credential currently in use")
  .action((_opts, cmd) => {
    void cmd;
    const cred = loadCredential();
    if (cred === null) {
      console.log("(no credential)");
      return;
    }
    console.log(JSON.stringify({ ...cred, secret: "***" }, null, 2));
  });

program
  .command("push")
  .description("Upload a trajectory")
  .requiredOption("--task <type>")
  .requiredOption("--model <model>")
  .requiredOption("--file <file>", "Path to trajectory JSON")
  .option("--parents <ids>", "Comma-separated parent experience IDs", "")
  .option("--uses-skill <name>", "Declare skill use (repeatable)", collect, [])
  .option("--sensitivity <s>", "low|medium|high", "medium")
  .option("--acl <acl>", "private|team:<X>", "private")
  .option("--tag <tag>", "Repeat to add tags", collect, [])
  .action(async (opts, cmd) => {
    const client = buildClient(cmd, true);
    const raw = JSON.parse(fs.readFileSync(opts.file, "utf-8"));
    const trajectory = Array.isArray(raw) ? raw : raw.trajectory ?? raw;
    const parents = (opts.parents as string)
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    const result = await client.request<unknown>("POST", "/v1/experiences", {
      task_type: opts.task,
      source_model: opts.model,
      trajectory,
      parent_experience_ids: parents,
      uses_skills: opts.usesSkill ?? [],
      sensitivity: opts.sensitivity,
      acl: opts.acl,
      tags: opts.tag ?? [],
    });
    console.log(JSON.stringify(result, null, 2));
  });

program
  .command("search")
  .description("Search experiences")
  .requiredOption("--q <query>")
  .option("--task <task>")
  .option("--top-k <k>", "", (v) => parseInt(v, 10), 5)
  .option("--sort <mode>", "score|similarity|q_value|recency", "score")
  .action(async (opts, cmd) => {
    const client = buildClient(cmd, true);
    const params = new URLSearchParams({
      q: opts.q,
      top_k: String(opts.topK),
      sort: opts.sort,
    });
    if (opts.task) params.set("task_type", opts.task);
    const result = await client.request<unknown>(
      "GET",
      `/v1/experiences/search?${params.toString()}`,
    );
    console.log(JSON.stringify(result, null, 2));
  });

program
  .command("push-skill")
  .description("Upload a skill bundle (directory containing SKILL.md)")
  .requiredOption("--bundle <dir>")
  .option("--sensitivity <s>", "low|medium|high", "medium")
  .option("--acl <acl>", "private|team:<X>", "private")
  .option("--tag <tag>", "Repeat to add tags", collect, [])
  .action(async (opts, cmd) => {
    const client = buildClient(cmd, true);
    const bundleDir = path.resolve(opts.bundle);
    const tar = await import("node:child_process");
    // Build a deterministic tar.gz of the bundle. We rely on bsdtar / GNU tar.
    const proc = tar.spawnSync(
      "tar",
      ["-czf", "-", "-C", bundleDir, "."],
      { encoding: "buffer", maxBuffer: 64 * 1024 * 1024 },
    );
    if (proc.status !== 0) {
      throw new Error(`tar failed: ${proc.stderr.toString()}`);
    }
    const bundleB64 = proc.stdout.toString("base64");
    const result = await client.request<unknown>("POST", "/v1/skills", {
      bundle_b64: bundleB64,
      sensitivity: opts.sensitivity,
      acl: opts.acl,
      tags: opts.tag ?? [],
    });
    console.log(JSON.stringify(result, null, 2));
  });

program
  .command("search-skills")
  .description("Search uploaded skills")
  .requiredOption("--q <query>")
  .option("--top-k <k>", "", (v) => parseInt(v, 10), 5)
  .action(async (opts, cmd) => {
    const client = buildClient(cmd, true);
    const params = new URLSearchParams({ q: opts.q, top_k: String(opts.topK) });
    const result = await client.request<unknown>(
      "GET",
      `/v1/skills/search?${params.toString()}`,
    );
    console.log(JSON.stringify(result, null, 2));
  });

program
  .command("install-skill")
  .description("Download and extract a skill bundle into a target directory")
  .requiredOption("--name <name>")
  .requiredOption("--target <dir>")
  .option("--version <v>")
  .action(async (opts, cmd) => {
    const client = buildClient(cmd, true);
    const params = new URLSearchParams({ name: opts.name });
    if (opts.version) params.set("version", opts.version);
    const result = await client.request<{
      skill_id: string;
      name: string;
      version: string;
      bundle_b64: string;
    }>("GET", `/v1/skills/install?${params.toString()}`);
    const target = path.resolve(opts.target);
    fs.mkdirSync(target, { recursive: true });
    const tar = await import("node:child_process");
    const proc = tar.spawnSync(
      "tar",
      ["-xzf", "-", "-C", target],
      { input: Buffer.from(result.bundle_b64, "base64") },
    );
    if (proc.status !== 0) {
      throw new Error(`extract failed: ${proc.stderr?.toString()}`);
    }
    console.log(
      JSON.stringify(
        { name: result.name, version: result.version, target },
        null,
        2,
      ),
    );
  });

program
  .command("prepare")
  .description("v0 lite: locally sanitize + structure a trajectory file (no upload)")
  .requiredOption("--file <file>", "Trajectory JSON")
  .option("--task <type>", "", "misc")
  .option("--model <model>", "", "unknown")
  .option("--sensitivity <s>", "low|medium|high", "medium")
  .option("--acl <acl>", "private|team:<X>", "private")
  .option("--tag <tag>", "Repeat to add tags", collect, [])
  .action((opts) => {
    const raw = JSON.parse(fs.readFileSync(opts.file, "utf-8"));
    const trajectory = Array.isArray(raw) ? raw : raw.trajectory ?? raw;
    const card = prepareLocalRuleBased(trajectory, {
      task_type: opts.task,
      source_model: opts.model,
      sensitivity: opts.sensitivity,
      acl: opts.acl,
      tags: opts.tag ?? [],
    });
    console.log(JSON.stringify(card, null, 2));
  });

program
  .command("push-lite")
  .description("v0 lite: prepare locally + upload (skips server-side judge/credit)")
  .requiredOption("--file <file>", "Trajectory JSON")
  .option("--task <type>", "", "misc")
  .option("--model <model>", "", "unknown")
  .option("--sensitivity <s>", "low|medium|high", "medium")
  .option("--acl <acl>", "private|team:<X>", "private")
  .option("--tag <tag>", "Repeat to add tags", collect, [])
  .option("--no-trace", "Send only the compressed LiteCard, drop the raw trajectory")
  .action(async (opts, cmd) => {
    const client = buildClient(cmd, true);
    const raw = JSON.parse(fs.readFileSync(opts.file, "utf-8"));
    const trajectory = Array.isArray(raw) ? raw : raw.trajectory ?? raw;
    const card = prepareLocalRuleBased(trajectory, {
      task_type: opts.task,
      source_model: opts.model,
      sensitivity: opts.sensitivity,
      acl: opts.acl,
      tags: opts.tag ?? [],
    });
    // Default: include raw trajectory so the server stores it for the
    // Trajectory tab. Pass --no-trace to keep the old card-only behavior.
    const body = opts.trace === false
      ? card
      : { ...card, trajectory };
    const result = await client.request<unknown>("POST", "/v1/lite/push", body);
    console.log(JSON.stringify(result, null, 2));
  });

program
  .command("search-lite")
  .description("v0 lite: pure cosine search, ACL-filtered, top-k")
  .requiredOption("--q <query>")
  .option("--top-k <k>", "", (v) => parseInt(v, 10), 5)
  .option("--task <task>")
  .action(async (opts, cmd) => {
    const client = buildClient(cmd, true);
    const result = await client.request<unknown>("POST", "/v1/lite/search", {
      q: opts.q,
      top_k: opts.topK,
      task_type: opts.task,
    });
    console.log(JSON.stringify(result, null, 2));
  });

program
  .command("dashboard")
  .description("Pool-wide health snapshot")
  .action(async (_opts, cmd) => {
    const client = buildClient(cmd, true);
    console.log(
      JSON.stringify(
        await client.request<unknown>("GET", "/v1/admin/dashboard"),
        null,
        2,
      ),
    );
  });

program
  .command("leaderboard")
  .description("Top-K most reused experiences")
  .option("--top-k <k>", "", (v) => parseInt(v, 10), 20)
  .action(async (opts, cmd) => {
    const client = buildClient(cmd, true);
    console.log(
      JSON.stringify(
        await client.request<unknown>(
          "GET",
          `/v1/admin/leaderboard?top_k=${opts.topK}`,
        ),
        null,
        2,
      ),
    );
  });

function collect(value: string, prev: string[]): string[] {
  return [...prev, value];
}

await program.parseAsync(process.argv).catch((err) => {
  console.error(String(err?.message ?? err));
  process.exit(1);
});
