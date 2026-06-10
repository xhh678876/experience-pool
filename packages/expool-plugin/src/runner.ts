import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import type { Config } from "./config.js";

// 与 Python 版 _run 返回结构对齐：下游 / 命令 .md 依赖 {ok, exit_code, result, stderr_tail}。
export interface CommandResult {
  ok: boolean;
  exit_code?: number;
  result: unknown;
  stderr_tail?: string;
  error?: string;
  remedy?: string;
}

export interface RunOpts {
  timeoutMs?: number;
  extraEnv?: Record<string, string>;
  requireKey?: boolean;
}

// ★迁移关键抽象：阶段1 = SubprocessRunner（spawn python uploader）；
// 阶段2 uploader 移植 TS 后，换成 InProcessRunner（直接调函数），工具定义零改动。
export interface CommandRunner {
  run(args: string[], opts?: RunOpts): Promise<CommandResult>;
}

export class SubprocessRunner implements CommandRunner {
  /** 由 index.ts 注入：() => isCredConfigured(this)。延迟设置以打破循环依赖。 */
  credCheck?: () => Promise<boolean>;

  constructor(private cfg: Config) {}

  async run(args: string[], opts: RunOpts = {}): Promise<CommandResult> {
    if (opts.requireKey !== false && this.credCheck) {
      if (!(await this.credCheck())) {
        return {
          ok: false,
          result: null,
          error: "no API key configured. Run /expool:bind to set up your credential.",
          remedy: "/expool:bind",
        };
      }
    }

    const env = { ...process.env, ...this.cfg.subprocessEnv(), ...(opts.extraEnv ?? {}) };
    const fullArgs = [this.cfg.vendoredCli, "--base", this.cfg.base, ...args];
    const timeoutMs = opts.timeoutMs ?? 120_000;
    if (!existsSync(this.cfg.vendoredCli)) {
      return {
        ok: false,
        result: null,
        error: `vendored CLI not found: ${this.cfg.vendoredCli}`,
        remedy: "Set EXPOOL_VENDOR_CLI to dist-public/exp_uploader.py or reinstall the plugin.",
      };
    }

    return await new Promise<CommandResult>((resolve) => {
      const proc = spawn("python3", fullArgs, { env });
      let out = "";
      let err = "";
      const timer = setTimeout(() => {
        proc.kill("SIGKILL");
        resolve({ ok: false, result: null, error: `vendored CLI timed out after ${timeoutMs}ms` });
      }, timeoutMs);

      proc.stdout.on("data", (d) => (out += d));
      proc.stderr.on("data", (d) => (err += d));
      proc.on("error", (e) => {
        clearTimeout(timer);
        resolve({ ok: false, result: null, error: String(e) });
      });
      proc.on("close", (code) => {
        clearTimeout(timer);
        const trimmed = out.trim();
        let parsed: unknown = trimmed;
        try {
          parsed = JSON.parse(trimmed);
        } catch {
          /* 非 JSON 输出原样返回 */
        }
        const tail = err.trim().split("\n").slice(-20).join("\n");
        resolve({
          ok: code === 0,
          exit_code: code ?? -1,
          result: parsed,
          ...(tail ? { stderr_tail: tail } : {}),
        });
      });
    });
  }
}
