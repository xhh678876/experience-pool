import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import Link from "@/components/ui/link";
import { withBase } from "@/components/ui/link";
import RegisterForm from "./RegisterForm";

export const dynamic = "force-dynamic";

export default async function RegisterPage() {
  return (
    <div className="max-w-md mx-auto">
      <Card>
        <CardHeader>
          <CardTitle>注册账号</CardTitle>
          <CardDescription>
            使用邮箱 + 自设密码注册。
            注册后自动登录,主页会显示给你专属的 curl 绑定脚本 ——
            把它复制到任意 agent(Claude Code / Cursor / ...)的 shell,该 agent
            之后上传的所有 trace 都会归属到你的账号下。
          </CardDescription>
        </CardHeader>
        <CardContent>
          <RegisterForm />
          <p className="mt-4 text-xs text-muted-foreground">
            已有账号?
            <Link href="/login" className="ml-1 text-cyan-700 hover:underline">
              去登录
            </Link>
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
