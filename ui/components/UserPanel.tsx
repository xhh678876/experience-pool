import Link from "@/components/ui/link";
import { LogIn, LogOut, UserPlus, ShieldCheck } from "lucide-react";
import { apiMe } from "@/lib/users-api";
import { logoutAction } from "@/app/login/actions";

export default async function UserPanel() {
  const me = await apiMe();
  if (me) {
    return (
      <div className="flex items-center gap-2 text-xs">
        <span
          className="inline-flex h-7 items-center gap-1.5 rounded-md border border-cyan-600/25 bg-cyan-50/70 px-2 font-mono"
          title={`agent: ${me.default_agent_name}`}
        >
          <ShieldCheck className="h-3.5 w-3.5 text-cyan-700" />
          {me.email}
        </span>
        <form action={logoutAction}>
          <button
            type="submit"
            className="inline-flex h-7 items-center gap-1 rounded-md border border-border/70 bg-background px-2 text-muted-foreground hover:border-rose-500/40 hover:text-rose-700"
          >
            <LogOut className="h-3.5 w-3.5" />
            登出
          </button>
        </form>
      </div>
    );
  }
  return (
    <div className="flex items-center gap-1.5 text-xs">
      <Link
        href="/login"
        className="inline-flex h-7 items-center gap-1 rounded-md border border-border/70 bg-background px-2 text-muted-foreground hover:border-cyan-600/40 hover:bg-cyan-50/40 hover:text-cyan-800"
      >
        <LogIn className="h-3.5 w-3.5" />
        登录
      </Link>
      <Link
        href="/register"
        className="inline-flex h-7 items-center gap-1 rounded-md border border-cyan-600/40 bg-cyan-600 px-2 font-medium text-white hover:bg-cyan-700"
      >
        <UserPlus className="h-3.5 w-3.5" />
        注册
      </Link>
    </div>
  );
}
