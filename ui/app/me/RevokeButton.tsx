"use client";

import { useState, useTransition } from "react";
import { Trash2, Loader2, Check, X } from "lucide-react";
import { revokeAction } from "./actions";

interface Props {
  experienceId: string;
  onRevoked?: () => void;
}

export default function RevokeButton({ experienceId, onRevoked }: Props) {
  const [confirming, setConfirming] = useState(false);
  const [result, setResult] = useState<
    { ok: boolean; message: string } | null
  >(null);
  const [pending, startTransition] = useTransition();

  if (result?.ok) {
    return (
      <span className="inline-flex items-center gap-1 rounded-md border border-emerald-500/40 bg-emerald-50 px-2 py-1 text-[11px] text-emerald-800">
        <Check className="h-3 w-3" />
        revoked
      </span>
    );
  }

  if (!confirming) {
    return (
      <button
        type="button"
        onClick={() => setConfirming(true)}
        className="inline-flex items-center gap-1 rounded-md border border-rose-500/30 bg-rose-50 px-2 py-1 text-[11px] text-rose-700 transition hover:bg-rose-100"
        title="Hard-delete this experience: trajectory file unlinked, row marked revoked, vectors + cluster + rewards purged"
      >
        <Trash2 className="h-3 w-3" />
        revoke
      </button>
    );
  }

  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="text-[11px] text-rose-700 font-medium">delete?</span>
      <button
        type="button"
        disabled={pending}
        onClick={() => {
          startTransition(async () => {
            const fd = new FormData();
            fd.set("eid", experienceId);
            fd.set("reason", "ui_revoke_button");
            const r = await revokeAction(undefined, fd);
            setResult({ ok: r.ok, message: r.message });
            setConfirming(false);
            if (r.ok) onRevoked?.();
          });
        }}
        className="inline-flex items-center gap-1 rounded-md bg-rose-600 px-2 py-1 text-[11px] font-medium text-white hover:bg-rose-700 disabled:opacity-50"
      >
        {pending ? (
          <Loader2 className="h-3 w-3 animate-spin" />
        ) : (
          <Trash2 className="h-3 w-3" />
        )}
        confirm
      </button>
      <button
        type="button"
        onClick={() => setConfirming(false)}
        disabled={pending}
        className="inline-flex items-center gap-1 rounded-md border border-border/60 bg-white px-2 py-1 text-[11px] text-muted-foreground hover:bg-muted/40"
      >
        <X className="h-3 w-3" />
        cancel
      </button>
      {result && !result.ok ? (
        <span className="text-[11px] text-rose-700">{result.message}</span>
      ) : null}
    </span>
  );
}
