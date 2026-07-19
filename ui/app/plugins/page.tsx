import Link from "@/components/ui/link";
import { CopyButton } from "@/components/ui/copy-button";
import { Badge } from "@/components/ui/badge";
import {
  ChevronDown,
  Download,
  Github,
  Key,
  PackageCheck,
  Plug,
  Radar,
  Search,
  ShieldCheck,
  Sparkles,
  Terminal,
  ToggleLeft,
  UploadCloud,
} from "lucide-react";
import { publicGatewayBase, publicUiBase } from "@/lib/public-url";
import { apiPluginPackage } from "@/lib/users-api";

export const dynamic = "force-dynamic";

const NPM_PACKAGE = process.env.EXP_PLUGIN_NPM_PACKAGE ?? "@haohui666/expool-plugin";
const PLUGIN_REPO_URL = (
  process.env.EXP_PLUGIN_REPO_URL ?? "https://github.com/xhh678876/expool-mcp-plugin"
).trim().replace(/\.git$/, "");

function gitInstallUrl(repoUrl: string): string {
  return repoUrl.endsWith(".git") ? repoUrl : `${repoUrl}.git`;
}

export default async function PluginsPage() {
  const base = publicGatewayBase();
  const pluginPackage = await apiPluginPackage();
  const meUrl = `${publicUiBase()}/me/api-keys`;
  const npmInstall = `npx ${NPM_PACKAGE} install`;
  const githubInstall = PLUGIN_REPO_URL ? `npx --yes git+${gitInstallUrl(PLUGIN_REPO_URL)} install` : "";
  const terminalPair = `expool-plugin pair expair_...`;
  const terminalBind = `expool-plugin bind+api expk_...`;
  const terminalDetect = `expool-plugin detect`;
  const terminalAutoOn = `expool-plugin auto on --sources claude-code,codex,hermes --interval 120`;
  const terminalAutoOff = `expool-plugin auto off`;
  const terminalAutoStatus = `expool-plugin auto status`;

  return (
    <div className="flex flex-col gap-6 pb-12">
      <section className="rounded-2xl border border-border/60 bg-white/85 px-5 py-4">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Plug className="h-4 w-4 text-cyan-700" />
          <span className="font-semibold">Experience Pool 插件</span>
          <Badge variant="outline" className="font-mono text-[10px]">
            Claude Code · Codex · OpenClaw · Hermes
          </Badge>
          {pluginPackage ? (
            <Badge variant="outline" className="font-mono text-[10px]">
              v{pluginPackage.version} · {formatBytes(pluginPackage.size_bytes)}
            </Badge>
          ) : null}
        </div>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
          装上插件，就能在 Claude Code / Codex / OpenClaw / Hermes 这些 agent 里直接用
          <code className="mx-1 font-mono">/expool:...</code>
          斜杠命令。流程是：先<b>装一次</b>，再<b>绑定一次账号</b>，之后开<b>自动上传</b>就好了。所有上传默认 private，只有你自己能看到，
          <Link href="/me" className="mx-1 text-cyan-700 hover:underline">/me</Link>
          页能撤回。
        </p>
        <div className="mt-4 grid gap-2 sm:grid-cols-3">
          <PluginFact
            icon={<Github className="h-4 w-4" />}
            label="官方源码仓库"
            value={PLUGIN_REPO_URL.replace(/^https?:\/\//, "")}
            href={PLUGIN_REPO_URL}
          />
          <PluginFact
            icon={<PackageCheck className="h-4 w-4" />}
            label="npm 包"
            value={pluginPackage?.name ?? NPM_PACKAGE}
          />
          <PluginFact
            icon={<Download className="h-4 w-4" />}
            label="内网分发"
            value={pluginPackage ? `${pluginPackage.filename} · v${pluginPackage.version}` : "等待生成 expool.tgz"}
          />
        </div>
      </section>

      <Section
        icon={<Download className="h-4 w-4 text-cyan-700" />}
        title="① 安装插件"
        subtitle="一行命令搞定。自动识别本机已装的 agent（Claude Code / Codex / OpenClaw / Hermes），没装的会自动跳过。已装过可重复跑，自动更新到最新版。"
      >
        <PrimaryCommand
          label="npm 安装（推荐）"
          description="走 npmjs 公网，最新版自动拉。出差换机器、CI 部署都能用。"
          command={npmInstall}
        />
        {pluginPackage?.install_script_command ? (
          <CommandRow
            label="一键脚本安装"
            description="走当前内网 gateway 下载 /plugins/install.sh，自动校验 tarball sha256 并注册 Claude Code / Codex / OpenClaw / Hermes。"
            command={pluginPackage.install_script_command}
          />
        ) : null}
        {pluginPackage?.install_command ? (
          <CommandRow
            label="手动包安装"
            description="不想直接 curl | bash 时用。先下载 /plugins/expool.tgz，再 npm install -g 本地包。"
            command={pluginPackage.install_command}
          />
        ) : null}

        <details className="group rounded-lg border border-border/60 bg-white/50">
          <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-2 text-xs text-muted-foreground">
            <ChevronDown className="h-3.5 w-3.5 transition group-open:rotate-180" />
            <span>其他安装方式（不需要可忽略）</span>
          </summary>
          <div className="space-y-2 px-4 pb-4 pt-2">
            {githubInstall ? (
              <CommandRow
                label="GitHub 源码安装"
                description="直接从 git 源拉。开发分支调试、想看插件源码时用。"
                command={githubInstall}
              />
            ) : null}
            <p className="px-1 pt-1 text-xs text-muted-foreground">
              想只装某一个 agent？把 <code className="font-mono">--agents claude,codex,openclaw,hermes</code>
              里改成你要的（比如只留 <code className="font-mono">claude</code>，或逗号分隔多选）。
            </p>
          </div>
        </details>
      </Section>

      <Section
        icon={<Key className="h-4 w-4 text-cyan-700" />}
        title="② 绑定账号"
        subtitle="一次性绑定本机到你的账号。推荐用配对码，避免把完整 API key 粘进聊天框。"
      >
        <CommandRow
          label="agent 内配对码绑定（推荐）"
          description={
            <>
              先去
              <Link href="/me/api-keys" className="mx-1 text-cyan-700 hover:underline">
                /me/api-keys
              </Link>
              生成一次性 <code className="font-mono">expair_...</code> 配对码，再在 agent 里粘进来。配对码是一次性的，泄露也能立刻作废。
            </>
          }
          command="/expool:pair expair_..."
        />
        <CommandRow
          label="agent 内 API Key 绑定"
          description="跳过配对码，直接粘 expk_ 长 API key。适合脚本化部署、或不方便开网页的场景。注意 expk_ 是长期凭据，别贴到截图/日志里。"
          command="/expool:bind expk_..."
        />
        <CommandRow
          label="终端配对（不进 agent）"
          description="在 shell 里直接配对，不进 agent。适合脚本化批量部署。"
          command={terminalPair}
        />
        <CommandRow
          label="终端 API Key 绑定（不进 agent）"
          description="在 shell 里直接用 API key 绑定。结果跟在 agent 里跑 /expool:bind 一样。"
          command={terminalBind}
        />
        <CommandRow
          label="账号入口"
          description="生成 expair 配对码 / 管理 expk API key 的网页地址。"
          command={meUrl}
        />
      </Section>

      <Section
        icon={<UploadCloud className="h-4 w-4 text-cyan-700" />}
        title="③ 日常使用"
        subtitle="装完绑完后，主要就用这几条命令。agent 内直接用斜杠就行。"
      >
        <CommandRow
          label="开工前先学一遍（推荐每个新任务的第一步）"
          description="自动在你的个人池里搜同类任务、读最高分卡片、出一份「学到了什么 + 接下来怎么做」的草案，等你点头再开始。能避免重新踩前人的坑。"
          command={'/expool:prep "修复 FastAPI HMAC 签名失败"'}
        />
        <CommandRow
          label="只检索，不出草案"
          description="想自己看搜索结果时用。/expool:prep 内部也会调它，但 prep 会额外读 top hit 全卡 + 给你出执行计划。"
          command={'/expool:search "FastAPI HMAC 验签"'}
        />
        <CommandRow
          label="手动上传当前 session"
          description="把这段对话当作一个任务归档到你的 private 库。&lt;task-name&gt; 是 kebab-case 短描述，比如 fix-cors-bug。"
          command="/expool:upload <task-name>"
        />
        <CommandRow
          label="检测本机可识别的 runtime"
          description="看看本机有 Claude Code、Codex、OpenClaw、Hermes 哪些 agent 的会话数据可被插件读取。"
          command={terminalDetect}
        />
      </Section>

      <Section
        icon={<ToggleLeft className="h-4 w-4 text-cyan-700" />}
        title="④ 自动上传开关"
        subtitle="开了之后每个任务结束自动归档到 private，再也不用每次手动跑 /expool:upload。"
      >
        <CommandRow
          label="开启自动上传（agent 内）"
          description="开启后，本机 scheduler 每 120 秒扫描一次新 session，自动归档到 private。可指定监听哪些 source。"
          command="/expool:auto-on --sources claude-code,codex,hermes"
        />
        <CommandRow
          label="关闭自动上传（agent 内）"
          description="停掉自动归档。已经上传的不会被撤回——要撤回去 /me 页面操作。"
          command="/expool:auto-off"
        />
        <CommandRow
          label="开启自动上传（终端）"
          description="在 shell 里直接开。--interval 控制扫描间隔（秒）。结果跟 agent 内 /expool:auto-on 一样。"
          command={terminalAutoOn}
        />
        <CommandRow
          label="关闭自动上传（终端）"
          description="在 shell 里直接关。"
          command={terminalAutoOff}
        />
        <CommandRow
          label="查询调度器状态"
          description="看 scheduler 是开是关、上次上传时间、绑定到哪个账号。出问题时第一个跑这个排查。"
          command={terminalAutoStatus}
        />
      </Section>

      <Section
        icon={<ShieldCheck className="h-4 w-4 text-emerald-700" />}
        title="默认安全边界"
        subtitle="插件在隐私和权限上的默认行为，没你点头之前不会变。"
      >
        <BulletList
          items={[
            <>API key 存在本机 <code className="font-mono">~/.config/expool</code>，<b>不会上传</b>到服务器。</>,
            <>所有上传默认 <code className="font-mono">acl=private</code>，只有你自己能看到；不会自动进社区池。</>,
            <>
              要发布到社区池必须去
              <Link href="/me" className="mx-1 text-cyan-700 hover:underline">/me</Link>
              页面手动确认，agent 不会代劳。撤回也在同一页。
            </>,
            <>自动上传可以随时开/关，状态查询：<code className="font-mono">{terminalAutoStatus}</code>。</>,
          ]}
        />
      </Section>
    </div>
  );
}

function Section({
  icon,
  title,
  subtitle,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-border/60 bg-white/85">
      <div className="border-b border-border/60 px-5 py-3">
        <div className="flex items-center gap-2 text-sm font-semibold">
          {icon}
          <span>{title}</span>
        </div>
        {subtitle ? (
          <p className="mt-1 text-xs leading-5 text-muted-foreground">{subtitle}</p>
        ) : null}
      </div>
      <div className="space-y-2 px-5 py-4">{children}</div>
    </section>
  );
}

function BulletList({ items }: { items: React.ReactNode[] }) {
  return (
    <ul className="space-y-2 text-sm leading-6 text-muted-foreground">
      {items.map((item, idx) => (
        <li key={idx} className="flex gap-2">
          <span className="mt-2 inline-block h-1 w-1 shrink-0 rounded-full bg-cyan-700" />
          <span className="min-w-0">{item}</span>
        </li>
      ))}
    </ul>
  );
}

function PluginFact({
  icon,
  label,
  value,
  href,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  href?: string;
}) {
  const text = (
    <span className="block truncate font-mono text-[11px] text-foreground">
      {value}
    </span>
  );
  return (
    <div className="min-w-0 rounded-lg border border-border/60 bg-white/70 px-3 py-2.5">
      <div className="mb-1 flex items-center gap-1.5 text-[11px] text-muted-foreground">
        <span className="text-cyan-800">{icon}</span>
        <span>{label}</span>
      </div>
      {href ? (
        <a href={href} target="_blank" rel="noreferrer" className="hover:text-cyan-800 hover:underline">
          {text}
        </a>
      ) : (
        text
      )}
    </div>
  );
}

function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "0 B";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function PrimaryCommand({
  label,
  description,
  command,
}: {
  label: string;
  description?: React.ReactNode;
  command: string;
}) {
  return (
    <div className="rounded-xl border-2 border-cyan-600/40 bg-cyan-50/40 px-5 py-4">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-cyan-700" />
            <span className="text-sm font-semibold text-foreground">{label}</span>
            <span className="rounded-full bg-cyan-700 px-2 py-0.5 text-[10px] font-medium text-white">
              推荐
            </span>
          </div>
          {description ? (
            <p className="mt-1 text-xs leading-5 text-muted-foreground">{description}</p>
          ) : null}
        </div>
        <CopyButton text={command} label="复制" className="h-9 shrink-0 px-3 text-sm" />
      </div>
      <code className="block w-full whitespace-pre-wrap break-all rounded-md border border-cyan-600/30 bg-white/90 px-3 py-3 font-mono text-xs leading-5 text-foreground">
        {command}
      </code>
    </div>
  );
}

function CommandRow({
  label,
  description,
  command,
}: {
  label: string;
  description?: React.ReactNode;
  command: string;
}) {
  return (
    <div className="rounded-lg border border-border/60 bg-white/70 px-4 py-3">
      <div className="mb-2 flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-foreground">{label}</div>
          {description ? (
            <p className="mt-0.5 text-xs leading-5 text-muted-foreground">{description}</p>
          ) : null}
        </div>
        <CopyButton text={command} label="复制" className="h-8 shrink-0 px-3" />
      </div>
      <code className="block w-full whitespace-pre-wrap break-all rounded-md border border-border/60 bg-muted/30 px-3 py-2 font-mono text-xs leading-5 text-foreground">
        {command}
      </code>
    </div>
  );
}

function intranetInstallCommand(tarballUrl: string, base: string): string {
  return `tmp="\${TMPDIR:-/tmp}/expool-plugin.tgz" && curl --noproxy '*' -fsSL ${tarballUrl} -o "$tmp" && npm install -g "$tmp" && expool-plugin install --agents claude,codex,openclaw,hermes --base ${base} --force`;
}

function intranetScriptInstallCommand(base: string): string {
  return `curl --noproxy '*' -fsSL ${base}/plugins/install.sh | bash`;
}
