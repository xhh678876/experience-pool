import { redirect } from "next/navigation";
import { cookies } from "next/headers";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

async function setReviewer(formData: FormData): Promise<void> {
  "use server";
  const name = (formData.get("name") as string | null)?.trim() ?? "";
  const c = await cookies();
  if (name.length === 0) {
    c.delete("X-Reviewer-Name");
  } else {
    c.set("X-Reviewer-Name", name, {
      httpOnly: false,
      sameSite: "lax",
      path: "/",
      maxAge: 60 * 60 * 24 * 30,
    });
  }
  redirect("/");
}

export default async function LoginPage() {
  const c = await cookies();
  const current = c.get("X-Reviewer-Name")?.value ?? "";
  return (
    <div className="max-w-md mx-auto">
      <Card>
        <CardHeader>
          <CardTitle>切换审阅身份</CardTitle>
          <CardDescription>
            这里不做密码校验，只把审阅人名称写入 cookie，并记录到后续操作审计里。留空可清除。
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form action={setReviewer} className="space-y-4">
            <Input
              name="name"
              defaultValue={current}
              placeholder="例如：alice"
              autoFocus
            />
            <div className="flex justify-end">
              <Button type="submit">保存</Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
