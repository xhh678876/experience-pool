import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { existsSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";

const DEFAULT_BASE = "http://127.0.0.1:3080";
const PACKAGE_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const REPO_ROOT = resolve(PACKAGE_ROOT, "..", "..");

// EXP_UI_PUBLIC_URL 形如 .../proxy/3002 时，推导出网关 .../proxy/3080。
function gatewayFromUiPublicUrl(): string | undefined {
  const ui = (process.env.EXP_UI_PUBLIC_URL ?? "").replace(/\/$/, "");
  const marker = "/proxy/";
  const idx = ui.lastIndexOf(marker);
  if (idx < 0) return undefined;
  return `${ui.slice(0, idx)}/proxy/3080`;
}

function firstExisting(paths: string[]): string | undefined {
  return paths.find((p) => existsSync(p));
}

function defaultVendorCli(): string {
  return (
    firstExisting([
      join(PACKAGE_ROOT, "vendor", "exp_uploader.py"),
      join(REPO_ROOT, "dist-public", "exp_uploader.py"),
    ]) || join(PACKAGE_ROOT, "vendor", "exp_uploader.py")
  );
}

function defaultPluginRoot(): string {
  return (
    firstExisting([
      join(PACKAGE_ROOT, "plugins", "expool"),
      PACKAGE_ROOT,
    ]) || PACKAGE_ROOT
  );
}

export interface Config {
  base: string;
  vendoredCli: string;
  credDir: string;
  stateRoot: string;
  pluginRoot: string;
  subprocessEnv(): Record<string, string>;
}

export function loadConfig(): Config {
  const base =
    (
      process.env.EXPOOL_BASE ||
      process.env.EXP_BIND_BASE_URL ||
      process.env.EXP_PUBLIC_BASE_URL ||
      gatewayFromUiPublicUrl() ||
      DEFAULT_BASE
    ).trim() || DEFAULT_BASE;

  const pluginRoot = process.env.EXPOOL_PLUGIN_ROOT || defaultPluginRoot();
  const vendoredCli = process.env.EXPOOL_VENDOR_CLI || defaultVendorCli();
  const credDir = process.env.EXPOOL_CRED_DIR || join(homedir(), ".config", "expool");
  const stateRoot = process.env.EXPOOL_STATE_ROOT || join(homedir(), ".local", "share", "expool");

  return {
    base,
    vendoredCli,
    credDir,
    stateRoot,
    pluginRoot,
    subprocessEnv() {
      mkdirSync(stateRoot, { recursive: true });
      // 与 Python 版一致：插件独立的 cred dir + state，避免和用户的 standalone daemon 抢。
      return {
        EXP_CRED_DIR: credDir,
        EXP_STATE_PATH: join(stateRoot, "state.json"),
      };
    },
  };
}
