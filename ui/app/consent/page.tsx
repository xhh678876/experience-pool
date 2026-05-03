import { Settings, FileLock2 } from "lucide-react";
import ConsentEditor from "./ConsentEditor";

export const dynamic = "force-static";

export default function ConsentPage() {
  return (
    <div className="flex flex-col gap-6 pb-12">
      <section className="flex flex-col gap-2 rounded-2xl border border-border/60 bg-white/85 px-5 py-4">
        <div className="flex items-center gap-2 text-sm">
          <Settings className="h-4 w-4 text-cyan-700" />
          <span className="font-semibold">Consent editor</span>
          <span className="text-muted-foreground">
            · 本地 consent.json 可视化编辑器
          </span>
        </div>
        <p className="text-xs leading-5 text-muted-foreground">
          consent.json 是<strong>纯本地文件</strong>，存在你机器的{" "}
          <code className="font-mono">~/.experience-pool/consent.json</code>
          。这个编辑器不会向服务器发送任何决策 — 只生成 JSON 让你下载。
          上传 / 下载 / 编辑全部发生在浏览器里。
        </p>
        <div className="flex items-start gap-2 rounded-md border border-cyan-500/30 bg-cyan-50/70 px-3 py-2 text-xs leading-5 text-cyan-900">
          <FileLock2 className="mt-0.5 h-3.5 w-3.5 flex-none" />
          <span>
            <strong>使用流程：</strong>
            <ol className="ml-4 list-decimal space-y-0.5">
              <li>
                <strong>Load consent.json</strong>{" "}
                上传你机器现有的 consent.json（可选）
              </li>
              <li>用下面的 UI 编辑 global / per-agent / cwd 规则</li>
              <li>
                <strong>Download</strong> 把新的 consent.json 保存下来，覆盖到{" "}
                <code className="font-mono">~/.experience-pool/consent.json</code>
              </li>
              <li>
                或者 <strong>Copy JSON</strong>{" "}
                后用 CLI 一行命令应用：
                <code className="ml-1 font-mono">
                  pbpaste &gt; ~/.experience-pool/consent.json
                </code>
              </li>
            </ol>
          </span>
        </div>
      </section>

      <ConsentEditor />
    </div>
  );
}
