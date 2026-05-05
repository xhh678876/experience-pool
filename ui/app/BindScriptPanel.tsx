import Link from "@/components/ui/link";
import { CopyButton } from "@/components/ui/copy-button";
import { apiBindScript, apiMe } from "@/lib/users-api";
import { logoutAction } from "./login/actions";

interface Props {
  /** Fallback command shown when no user is logged in (the generic install). */
  installCmdFallback: string;
}

export default async function BindScriptPanel({ installCmdFallback }: Props) {
  const me = await apiMe();

  if (!me) {
    return (
      <div className="mt-6 w-full">
        <div className="mb-2 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
          一行命令安装
        </div>
        <div className="flex flex-col items-stretch gap-2 sm:flex-row sm:items-center sm:justify-center">
          <code className="flex-1 truncate rounded-lg border border-border/60 bg-white/85 px-3 py-2.5 text-center font-mono text-[12px] text-foreground sm:text-sm">
            {installCmdFallback}
          </code>
          <CopyButton text={installCmdFallback} label="复制" className="h-10 px-4" />
        </div>
        <p className="mt-2 text-[11px] text-muted-foreground/80">
          这是一个未绑定身份的通用安装。
          <Link href="/register" className="ml-1 text-cyan-700 hover:underline">
            注册
          </Link>{" "}
          或{" "}
          <Link href="/login" className="text-cyan-700 hover:underline">
            登录
          </Link>{" "}
          后会显示带身份的绑定脚本,上传的 trace 会归属到你的账号。
        </p>
      </div>
    );
  }

  const bind = await apiBindScript();
  const cmd = bind?.bind_command ?? installCmdFallback;

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
        你的专属绑定脚本(复制到任一 agent 的 shell 即可)
      </div>
      <div className="flex flex-col items-stretch gap-2 sm:flex-row sm:items-center sm:justify-center">
        <code className="flex-1 truncate rounded-lg border border-cyan-600/40 bg-cyan-50/60 px-3 py-2.5 text-center font-mono text-[12px] text-foreground sm:text-sm">
          {cmd}
        </code>
        <CopyButton text={cmd} label="复制" className="h-10 px-4" />
      </div>
      <p className="mt-2 text-[11px] text-muted-foreground/80">
        这一行包含你的 HMAC secret,直接喂给 Claude Code / Cursor / Codex 等
        agent。之后该 agent 上传的 trace 默认 acl=private,
        <Link href="/me" className="ml-1 text-cyan-700 hover:underline">
          /me
        </Link>{" "}
        里点 "一键公开" 才进社区池。
      </p>
    </div>
  );
}
