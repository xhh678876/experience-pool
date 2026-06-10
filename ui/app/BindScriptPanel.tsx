import Link from "@/components/ui/link";
import { CopyButton } from "@/components/ui/copy-button";
import { apiBindScript, apiMe, apiPluginPackage } from "@/lib/users-api";
import { publicGatewayBase, publicUiBase } from "@/lib/public-url";
import { logoutAction } from "./login/actions";

const NPM_PACKAGE = process.env.EXP_PLUGIN_NPM_PACKAGE ?? "@chuangzhi/expool-plugin";
const PLUGIN_REPO_URL = (process.env.EXP_PLUGIN_REPO_URL ?? "").trim();

export default async function BindScriptPanel() {
  const me = await apiMe();
  const defaultBase = publicGatewayBase();
  const pluginPackage = await apiPluginPackage();
  const meUrl = `${publicUiBase()}/me`;

  if (!me) {
    const intranetScriptInstall = pluginPackage?.install_script_command;
    const intranetInstall = pluginPackage?.install_command;
    const npmInstall = `npx ${NPM_PACKAGE} install --agents claude,codex,openclaw,hermes --base ${defaultBase}`;
    const githubInstall = PLUGIN_REPO_URL
      ? `npx --yes git+${PLUGIN_REPO_URL}.git install --agents claude,codex,openclaw,hermes --base ${defaultBase}`
      : "";
    const detect = `expool-plugin detect --source auto`;
    const terminalAutoOn = `expool-plugin auto on --sources claude-code,codex,hermes --interval 120`;
    const terminalAutoOff = `expool-plugin auto off`;
    return (
      <div className="mt-6 w-full">
        <div className="mb-2 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
          插件安装
        </div>
        <div className="grid gap-2 text-left">
          <CommandBox label="npm 安装" command={npmInstall} accent={!intranetScriptInstall} />
          {intranetScriptInstall ? (
            <CommandBox label="一键脚本安装" command={intranetScriptInstall} accent />
          ) : null}
          {intranetInstall ? <CommandBox label="手动包安装" command={intranetInstall} /> : null}
          {githubInstall ? <CommandBox label="GitHub 源码安装" command={githubInstall} /> : null}
          <CommandBox label="账号入口" command={meUrl} />
          <CommandBox label="检测 session" command={detect} />
          <CommandBox label="终端自动开" command={terminalAutoOn} />
          <CommandBox label="终端自动关" command={terminalAutoOff} />
        </div>
        <p className="mt-2 text-[11px] text-muted-foreground/80">
          先装插件；登录后到
          <Link href="/me/api-keys" className="mx-1 text-cyan-700 hover:underline">
            API Key
          </Link>
          页面生成 <code className="font-mono">expair_...</code> 一次性绑定码，再运行
          <code className="mx-1 font-mono">/expool:pair expair_...</code>。
          <Link href="/register" className="ml-1 text-cyan-700 hover:underline">
            注册
          </Link>{" "}
          或{" "}
          <Link href="/login" className="text-cyan-700 hover:underline">
            登录
          </Link>{" "}
          后可以生成 API key，上传的 trace 会归属到你的账号。
        </p>
      </div>
    );
  }

  const bind = await apiBindScript();
  const base = bind?.base_url ?? defaultBase;
  const intranetScriptInstall = pluginPackage?.install_script_command;
  const intranetInstall = pluginPackage?.install_command;
  const npmInstall = `npx ${NPM_PACKAGE} install --agents claude,codex,openclaw,hermes --base ${base}`;
  const githubInstall = PLUGIN_REPO_URL
    ? `npx --yes git+${PLUGIN_REPO_URL}.git install --agents claude,codex,openclaw,hermes --base ${base}`
    : "";
  const terminalPair = `expool-plugin pair expair_... --base ${base}`;
  const terminalBind = `expool-plugin bind+api expk_... --base ${base}`;
  const slashPair = "/expool:pair expair_...";
  const slashBind = "/expool:bind expk_...";
  const slashBindApi = "/expool:bind-api expk_...";
  const slashBindPlusApi = "/expool:bind+api expk_...";
  const detect = `expool-plugin detect --source auto`;
  const slashAutoOn = "/expool:auto-on --sources claude-code,codex,hermes";
  const slashAutoOff = "/expool:auto-off";
  const terminalAutoOn = `expool-plugin auto on --sources claude-code,codex,hermes --interval 120`;
  const terminalAutoOff = `expool-plugin auto off`;
  const terminalAutoStatus = `expool-plugin auto status`;

  return (
    <div className="mt-6 w-full">
      <div className="mb-2 flex items-center justify-center gap-2 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
        <span>已登录 · {me.email}</span>
        <span className="text-muted-foreground/60">·</span>
        <span>
          agent ={" "}
          <code className="font-mono normal-case text-cyan-700">
            {me.default_agent_name}
          </code>
        </span>
        <span className="text-muted-foreground/60">·</span>
        <form action={logoutAction}>
          <button
            type="submit"
            className="text-[11px] uppercase tracking-wider text-muted-foreground hover:text-rose-700"
          >
            登出
          </button>
        </form>
      </div>
      <div className="mb-2 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
        插件安装与绑定
      </div>
      <div className="grid gap-2 text-left">
        <CommandBox label="npm 安装" command={npmInstall} accent={!intranetScriptInstall} />
        {intranetScriptInstall ? (
          <CommandBox label="一键脚本安装" command={intranetScriptInstall} accent />
        ) : null}
        {intranetInstall ? <CommandBox label="手动包安装" command={intranetInstall} /> : null}
        {githubInstall ? <CommandBox label="GitHub 源码安装" command={githubInstall} /> : null}
        <CommandBox label="账号入口" command={meUrl} />
        <CommandBox label="推荐绑定" command={slashPair} />
        <CommandBox label="终端配对" command={terminalPair} />
        <CommandBox label="插件内绑定" command={slashBind} />
        <CommandBox label="API 绑定别名" command={slashBindApi} />
        <CommandBox label="bind+api 别名" command={slashBindPlusApi} />
        <CommandBox label="终端绑定" command={terminalBind} />
        <CommandBox label="识别模型" command={detect} />
        <CommandBox label="agent 自动开" command={slashAutoOn} />
        <CommandBox label="agent 自动关" command={slashAutoOff} />
        <CommandBox label="终端自动开" command={terminalAutoOn} />
        <CommandBox label="终端自动关" command={terminalAutoOff} />
        <CommandBox label="自动状态" command={terminalAutoStatus} />
      </div>
      <p className="mt-2 text-[11px] text-muted-foreground/80">
        API key 只在
        <Link href="/me/api-keys" className="mx-1 text-cyan-700 hover:underline">
          /me/api-keys
        </Link>{" "}
        生成后显示一次；绑定后上传默认 acl=private，发布到社区仍需在
        <Link href="/me" className="mx-1 text-cyan-700 hover:underline">
          /me
        </Link>
        手动确认。
      </p>
    </div>
  );
}

function CommandBox({
  label,
  command,
  accent = false,
}: {
  label: string;
  command: string;
  accent?: boolean;
}) {
  return (
    <div className="flex flex-col items-stretch gap-2 sm:flex-row sm:items-center">
      <div className="w-28 shrink-0 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <code
        className={
          "flex-1 truncate rounded-lg border px-3 py-2.5 font-mono text-[12px] text-foreground sm:text-sm " +
          (accent
            ? "border-cyan-600/40 bg-cyan-50/60"
            : "border-border/60 bg-white/85")
        }
      >
        {command}
      </code>
      <CopyButton text={command} label="复制" className="h-10 px-4" />
    </div>
  );
}
