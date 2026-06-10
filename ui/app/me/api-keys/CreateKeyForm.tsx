"use client";

import { useActionState, useState } from "react";
import { useFormStatus } from "react-dom";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { CopyButton } from "@/components/ui/copy-button";
import { Key, AlertTriangle } from "lucide-react";
import { createApiKeyAction, type CreateKeyFormState } from "./actions";

export default function CreateKeyForm({ base }: { base: string }) {
  const [state, formAction] = useActionState<CreateKeyFormState | undefined, FormData>(
    createApiKeyAction,
    undefined,
  );
  const [dismissed, setDismissed] = useState(false);

  return (
    <div className="space-y-3">
      <form action={formAction} className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <Input
          name="name"
          required
          placeholder="名称，例如：codex-bot / cron-job / training-eval"
          maxLength={64}
          className="sm:max-w-md"
        />
        <SubmitBtn />
      </form>

      {state && !state.ok ? (
        <p className="text-sm text-rose-700">
          创建失败（HTTP {state.status}）: {state.message}
        </p>
      ) : null}

      {state?.ok && state.created && !dismissed ? (
        <div className="rounded-lg border border-amber-500/40 bg-amber-50 px-4 py-3 text-sm text-amber-950">
          <div className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 flex-none text-amber-700" />
            <div className="flex-1 space-y-2">
              <div className="font-semibold">
                key 已生成 — 只显示一次，立即复制保存
              </div>
              <div className="flex items-center gap-2">
                <Key className="h-3.5 w-3.5 flex-none text-amber-700" />
                <code className="block flex-1 truncate rounded-md border border-amber-500/30 bg-white/80 px-2 py-1 font-mono text-xs">
                  {state.created.api_key}
                </code>
                <CopyButton text={state.created.api_key} label="复制" className="h-8 px-3" />
              </div>
              <p className="text-xs text-amber-900/80">
                名称：<code className="font-mono">{state.created.name}</code> · 绑定 agent:{" "}
                <code className="font-mono">{state.created.agent_name}</code>
              </p>
              <p className="text-xs text-amber-900/80">
                插件绑定：
                <code className="ml-1 font-mono">/expool:bind {state.created.api_key}</code>
                <span className="ml-1">或</span>
                <code className="ml-1 font-mono">/expool:bind-api ...</code>
              </p>
              <div className="flex flex-wrap gap-2">
                <CopyButton
                  text={`/expool:bind ${state.created.api_key}`}
                  label="复制 slash bind"
                  className="h-8 px-3"
                />
                <CopyButton
                  text={`/expool:bind-api ${state.created.api_key}`}
                  label="复制 bind-api"
                  className="h-8 px-3"
                />
                <CopyButton
                  text={`/expool:bind+api ${state.created.api_key}`}
                  label="复制 bind+api"
                  className="h-8 px-3"
                />
                <CopyButton
                  text={`expool-plugin bind+api ${state.created.api_key} --base ${base}`}
                  label="复制终端 bind"
                  className="h-8 px-3"
                />
              </div>
              <div className="pt-1">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => setDismissed(true)}
                >
                  我已保存，关闭提示
                </Button>
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function SubmitBtn() {
  const { pending } = useFormStatus();
  return (
    <Button type="submit" disabled={pending}>
      {pending ? "生成中..." : "生成新 key"}
    </Button>
  );
}
