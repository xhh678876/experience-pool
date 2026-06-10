"use client";

import { useState } from "react";
import { Check, Copy } from "lucide-react";

export function CopyButton({
  text,
  label = "复制",
  className = "",
}: {
  text: string;
  label?: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);

  return (
    <button
      type="button"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          // graceful fallback: select-all via prompt
          window.prompt("复制以下内容:", text);
        }
      }}
      className={
        "inline-flex h-7 items-center gap-1 rounded-md border border-border/60 bg-white/80 px-2 text-[11px] " +
        "text-muted-foreground transition hover:border-cyan-600/40 hover:text-cyan-800 " +
        (copied ? "border-emerald-500/50 bg-emerald-50 text-emerald-800 " : "") +
        className
      }
    >
      {copied ? (
        <>
          <Check className="h-3 w-3" />
          <span>已复制</span>
        </>
      ) : (
        <>
          <Copy className="h-3 w-3" />
          <span>{label}</span>
        </>
      )}
    </button>
  );
}
