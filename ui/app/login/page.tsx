import { redirect } from "next/navigation";
import { KeyRound, LockKeyhole, PlugZap, ShieldCheck } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import Link from "@/components/ui/link";
import { withPublicBase } from "@/lib/base-path";
import { apiMe } from "@/lib/users-api";
import LoginForm from "./LoginForm";

export const dynamic = "force-dynamic";

interface PageProps {
  searchParams: Promise<{ next?: string }>;
}

export default async function LoginPage({ searchParams }: PageProps) {
  const sp = await searchParams;
  const nextPath = sp.next && sp.next.startsWith("/") ? sp.next : "/";
  // Already logged in? bounce to wherever they wanted to go.
  const me = await apiMe();
  if (me) redirect(withPublicBase(nextPath));

  return (
    <div className="mx-auto grid max-w-5xl gap-4 py-4 lg:grid-cols-[1fr_420px] lg:py-8">
      <section className="rounded-lg border border-border/70 bg-[linear-gradient(135deg,hsl(174_45%_96%),hsl(47_42%_95%))] p-5 shadow-sm lg:p-7">
        <div className="inline-flex items-center gap-2 rounded-md border border-cyan-700/20 bg-white/75 px-2.5 py-1 text-xs font-medium text-cyan-800">
          <ShieldCheck className="h-3.5 w-3.5" />
          private workspace
        </div>
        <h1 className="mt-4 max-w-xl text-2xl font-semibold leading-tight text-foreground sm:text-3xl">
          登录后管理你的经验、API Key 和 agent 绑定。
        </h1>
        <p className="mt-3 max-w-2xl text-sm leading-6 text-muted-foreground">
          每个账号默认拥有独立 private 池。你可以撤回上传、生成一次性配对码，或把确认干净的经验发布到社区池。
        </p>
        <div className="mt-6 grid gap-2 sm:grid-cols-3">
          <LoginCapability icon={<PlugZap className="h-4 w-4" />} title="绑定 agent" text="Claude Code / Codex / OpenClaw / Hermes" />
          <LoginCapability icon={<KeyRound className="h-4 w-4" />} title="生成密钥" text="一次性配对码或长期 API Key" />
          <LoginCapability icon={<LockKeyhole className="h-4 w-4" />} title="默认私有" text="上传先进入自己的 private 池" />
        </div>
      </section>

      <Card className="self-start">
        <CardHeader className="border-b">
          <CardTitle>账号登录</CardTitle>
          <CardDescription>
            使用注册邮箱登录。成功后会回到刚才访问的页面。
          </CardDescription>
        </CardHeader>
        <CardContent>
          <LoginForm nextPath={nextPath} />
          <p className="mt-4 text-xs text-muted-foreground">
            还没有账号?
            <Link href="/register" className="ml-1 text-cyan-700 hover:underline">
              去注册
            </Link>
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

function LoginCapability({
  icon,
  title,
  text,
}: {
  icon: React.ReactNode;
  title: string;
  text: string;
}) {
  return (
    <div className="min-h-[96px] rounded-lg border border-border/60 bg-white/75 p-3">
      <div className="mb-3 text-cyan-800">{icon}</div>
      <div className="text-sm font-medium text-foreground">{title}</div>
      <div className="mt-1 text-xs leading-5 text-muted-foreground">{text}</div>
    </div>
  );
}
