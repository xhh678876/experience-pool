"use client";

import { useActionState, useState } from "react";
import { useFormStatus } from "react-dom";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { CopyButton } from "@/components/ui/copy-button";
import { Link2, TimerReset } from "lucide-react";
import {
  createPairingCodeAction,
  type CreatePairingCodeFormState,
} from "./actions";

export default function CreatePairingCodeForm({ base }: { base: string }) {
  const [state, formAction] = useActionState<CreatePairingCodeFormState | undefined, FormData>(
    createPairingCodeAction,
    undefined,
  );
  const [dismissed, setDismissed] = useState(false);

  return (
    <div className="space-y-3">
      <form action={formAction} className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <Input
          name="name"
          defaultValue="plugin-pairing"
          maxLength={64}
          className="sm:max-w-md"
        />
        <input type="hidden" name="ttl_seconds" value="600" />
        <PairBtn />
      </form>

      {state && !state.ok ? (
        <p className="text-sm text-rose-700">
          创建失败（HTTP {state.status}）: {state.message}
        </p>
      ) : null}

      {state?.ok && state.created && !dismissed ? (
        <div className="rounded-lg border border-cyan-500/40 bg-cyan-50 px-4 py-3 text-sm text-cyan-950">
          <div className="flex items-start gap-2">
            <Link2 className="mt-0.5 h-4 w-4 flex-none text-cyan-700" />
            <div className="min-w-0 flex-1 space-y-2">
              <div className="font-semibold">一次性绑定码已生成</div>
              <div className="flex items-center gap-2">
                <code className="block flex-1 truncate rounded-md border border-cyan-500/30 bg-white/80 px-2 py-1 font-mono text-xs">
                  {state.created.code}
                </code>
                <CopyButton text={state.created.code} label="复制" className="h-8 px-3" />
              </div>
              <p className="flex items-center gap-1 text-xs text-cyan-900/80">
                <TimerReset className="h-3.5 w-3.5" />
                10 分钟内使用一次；插件换到 API key 后会只保存在本机。
              </p>
              <div className="flex flex-wrap gap-2">
                <CopyButton
                  text={`/expool:pair ${state.created.code}`}
                  label="复制 slash pair"
                  className="h-8 px-3"
                />
                <CopyButton
                  text={`expool-plugin pair ${state.created.code} --base ${base}`}
                  label="复制终端 pair"
                  className="h-8 px-3"
                />
              </div>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => setDismissed(true)}
              >
                关闭提示
              </Button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function PairBtn() {
  const { pending } = useFormStatus();
  return (
    <Button type="submit" disabled={pending}>
      {pending ? "生成中..." : "生成绑定码"}
    </Button>
  );
}
