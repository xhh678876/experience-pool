import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

export function shortId(id: string, head = 8): string {
  if (!id) return "";
  if (id.length <= head + 3) return id;
  return id.slice(0, head) + "...";
}

export function formatDate(s: string | null | undefined): string {
  if (!s) return "-";
  try {
    return new Date(s.includes("T") ? s : s + "Z").toLocaleString();
  } catch {
    return s;
  }
}

export function tryParseJson<T = unknown>(s: string | null | undefined): T | null {
  if (!s) return null;
  try {
    return JSON.parse(s) as T;
  } catch {
    return null;
  }
}

export function statusColor(status: string): string {
  switch (status) {
    case "approved":
      return "bg-green-100 text-green-900 border-green-300 dark:bg-green-950 dark:text-green-200 dark:border-green-800";
    case "rejected":
      return "bg-red-100 text-red-900 border-red-300 dark:bg-red-950 dark:text-red-200 dark:border-red-800";
    case "edited":
      return "bg-amber-100 text-amber-900 border-amber-300 dark:bg-amber-950 dark:text-amber-200 dark:border-amber-800";
    case "pending":
    default:
      return "bg-slate-100 text-slate-900 border-slate-300 dark:bg-slate-900 dark:text-slate-100 dark:border-slate-700";
  }
}

export function sensitivityColor(s: string): string {
  switch (s) {
    case "high":
      return "bg-red-100 text-red-900 border-red-300 dark:bg-red-950 dark:text-red-200 dark:border-red-800";
    case "medium":
      return "bg-amber-100 text-amber-900 border-amber-300 dark:bg-amber-950 dark:text-amber-200 dark:border-amber-800";
    case "low":
    default:
      return "bg-slate-100 text-slate-900 border-slate-300 dark:bg-slate-900 dark:text-slate-100 dark:border-slate-700";
  }
}
