"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { RefreshCw, RotateCw } from "lucide-react";

const STORAGE_KEY = "exp-auto-refresh";

export default function AutoRefresh({
  intervalMs = 5000,
}: {
  intervalMs?: number;
}) {
  const router = useRouter();
  const [enabled, setEnabled] = useState<boolean>(true);
  const [tick, setTick] = useState<number>(0);

  useEffect(() => {
    try {
      const v = localStorage.getItem(STORAGE_KEY);
      if (v === "0") setEnabled(false);
    } catch {
      // localStorage may be blocked under some proxy / private mode setups
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    const id = setInterval(() => {
      router.refresh();
      setTick((t) => t + 1);
    }, intervalMs);
    return () => clearInterval(id);
  }, [enabled, intervalMs, router]);

  function toggle() {
    setEnabled((v) => {
      const next = !v;
      try {
        localStorage.setItem(STORAGE_KEY, next ? "1" : "0");
      } catch {
        // ignore
      }
      return next;
    });
  }

  return (
    <button
      type="button"
      onClick={toggle}
      title={enabled ? `每 ${Math.round(intervalMs / 1000)}s 自动刷新（点击关闭）` : "已暂停（点击开启）"}
      className={
        "inline-flex h-7 items-center gap-1.5 rounded-md border px-2 text-xs transition " +
        (enabled
          ? "border-emerald-500/30 bg-emerald-50 text-emerald-700"
          : "border-border/60 bg-muted/35 text-muted-foreground hover:border-emerald-500/40 hover:text-emerald-700")
      }
    >
      {enabled ? (
        <RotateCw className={"h-3.5 w-3.5 " + (tick > 0 ? "animate-spin-once" : "")} />
      ) : (
        <RefreshCw className="h-3.5 w-3.5" />
      )}
      {enabled ? `自动刷新 ${Math.round(intervalMs / 1000)}s` : "已暂停"}
    </button>
  );
}
