import { redirect } from "next/navigation";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import Link from "@/components/ui/link";
import { withBase } from "@/components/ui/link";
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
  if (me) redirect(withBase(nextPath));

  return (
    <div className="max-w-md mx-auto">
      <Card>
        <CardHeader>
          <CardTitle>登录</CardTitle>
          <CardDescription>
            用注册时的 <code className="font-mono">@sii.edu.cn</code> 邮箱 + 密码登录。
            登录后主页会显示绑定 agent 的 curl 脚本。
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
