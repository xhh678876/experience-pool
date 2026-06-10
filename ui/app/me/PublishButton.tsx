"use client";

import { useState, useTransition } from "react";
import {
  Globe2,
  Loader2,
  Check,
  X,
  AlertTriangle,
  Lock,
} from "lucide-react";
import { publishAction, unpublishAction, type PublishFormState } from "./actions";

interface Props {
  experienceId: string;
  currentStatus: string; // 'private' | 'published' | 'rejected'
}

export default function PublishButton({ experienceId, currentStatus }: Props) {
  const [pending, startTransition] = useTransition();
  const [result, setResult] = useState<PublishFormState | null>(null);

  const isPublished = currentStatus === "published";

  // Already published — show unpublish option.
  if (isPublished) {
    return (
      <span className="inline-flex items-center gap-1.5">
        <span className="inline-flex items-center gap-1 rounded-md border border-emerald-500/40 bg-emerald-50 px-2 py-1 text-[11px] text-emerald-800">
          <Globe2 className="h-3 w-3" />
          已发布
        </span>
        <button
          type="button"
          disabled={pending}
          onClick={() => {
            startTransition(async () => {
              const fd = new FormData();
              fd.set("eid", experienceId);
              const r = await unpublishAction(undefined, fd);
              setResult(r);
            });
          }}
          className="inline-flex items-center gap-1 rounded-md border border-border/60 bg-white px-2 py-1 text-[11px] text-muted-foreground hover:bg-muted/40 disabled:opacity-50"
          title="取消发布，回到私人池"
        >
          {pending ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Lock className="h-3 w-3" />
          )}
          收回
        </button>
      </span>
    );
  }

  // Just got rejected — show details + retry option.
  if (result && !result.ok && result.blocking_hits.length > 0) {
    return (
      <details className="inline-block max-w-[380px]">
        <summary className="inline-flex cursor-pointer items-center gap-1 rounded-md border border-rose-500/40 bg-rose-50 px-2 py-1 text-[11px] text-rose-800">
          <AlertTriangle className="h-3 w-3" />
          被拦截 · 命中 {result.blocking_hits.length} 处（点开查看）
        </summary>
        <ul className="mt-1 max-h-40 overflow-y-auto rounded-md border border-rose-200 bg-rose-50/60 px-2 py-1 text-[11px]">
          {result.blocking_hits.slice(0, 8).map((h, i) => (
            <li key={i} className="border-b border-rose-200/50 py-1 last:border-0">
              <span className="font-mono text-rose-900">{h.rule}</span>
              <span className="mx-1 text-muted-foreground">·</span>
              <span className="text-muted-foreground">{h.location}</span>
              <div className="ml-2 truncate font-mono text-rose-700">
                {h.preview}
              </div>
            </li>
          ))}
          {result.blocking_hits.length > 8 ? (
            <li className="py-1 text-muted-foreground">
              ... 还有 {result.blocking_hits.length - 8} 处
            </li>
          ) : null}
        </ul>
        <button
          type="button"
          onClick={() => setResult(null)}
          className="mt-1 inline-flex items-center gap-1 rounded-md border border-border/60 bg-white px-2 py-0.5 text-[10px] text-muted-foreground hover:bg-muted/40"
        >
          <X className="h-3 w-3" />
          关闭详情
        </button>
      </details>
    );
  }

  // Just succeeded.
  if (result?.ok && result.status === "published") {
    return (
      <span className="inline-flex items-center gap-1 rounded-md border border-emerald-500/40 bg-emerald-50 px-2 py-1 text-[11px] text-emerald-800">
        <Check className="h-3 w-3" />
        发布成功（已发布 {result.publish_count} 条）
      </span>
    );
  }

  // Default: action button.
  return (
    <button
      type="button"
      disabled={pending}
      onClick={() => {
        startTransition(async () => {
          const fd = new FormData();
          fd.set("eid", experienceId);
          const r = await publishAction(undefined, fd);
          setResult(r);
        });
      }}
      className="inline-flex items-center gap-1 rounded-md border border-cyan-500/40 bg-cyan-50 px-2 py-1 text-[11px] text-cyan-900 transition hover:bg-cyan-100 disabled:opacity-50"
      title="发布到社区池前会跑一遍严格脱敏（屏蔽 file://、本地路径、localhost、UUID 等）"
    >
      {pending ? (
        <Loader2 className="h-3 w-3 animate-spin" />
      ) : (
        <Globe2 className="h-3 w-3" />
      )}
      发布到社区
    </button>
  );
}
