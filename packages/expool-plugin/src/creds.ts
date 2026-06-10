import type { CommandRunner } from "./runner.js";

// 与 Python 版上轮改造一致：以 vendor 的 whoami（纯本地读凭据、不联网）为权威门控。
// whoami 成功且返回对象 = 已配置；否则视为未配置。
export async function isCredConfigured(runner: CommandRunner): Promise<boolean> {
  const r = await runner.run(["whoami"], { requireKey: false, timeoutMs: 30_000 });
  return r.ok && r.result != null && typeof r.result === "object";
}
