"use client";

import { useActionState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { createProjectInviteAction, type InviteFormState } from "../actions";

export default function InviteForm({ project }: { project: string }) {
  const [state, action, pending] = useActionState<InviteFormState | undefined, FormData>(
    createProjectInviteAction,
    undefined,
  );

  return (
    <div className="rounded-lg border border-border/60 bg-white/85 p-4">
      <form action={action} className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <input type="hidden" name="project" value={project} />
        <div className="min-w-0 flex-1">
          <label className="mb-1 block text-xs text-muted-foreground">邀请邮箱</label>
          <Input name="email" type="email" placeholder="teammate@example.com" className="h-10" />
        </div>
        <div>
          <label className="mb-1 block text-xs text-muted-foreground">角色</label>
          <Select name="role" defaultValue="member" className="h-10 min-w-[110px]">
            <option value="member">member</option>
            <option value="admin">admin</option>
          </Select>
        </div>
        <Button type="submit" disabled={pending} className="h-10 px-4">
          {pending ? "生成中" : "生成邀请"}
        </Button>
      </form>
      {state?.message ? (
        <p className="mt-3 text-sm text-red-700">{state.message}</p>
      ) : null}
      {state?.invite ? (
        <div className="mt-3 rounded-md border border-cyan-200 bg-cyan-50 p-3">
          <p className="text-xs font-medium text-cyan-900">一次性邀请 token</p>
          <code className="mt-1 block break-all rounded bg-white px-2 py-1 text-xs text-cyan-950">
            {state.invite.token}
          </code>
          <p className="mt-1 text-xs text-cyan-800">
            发给 {state.invite.email}，对方登录后在项目池页面粘贴接受。
          </p>
        </div>
      ) : null}
    </div>
  );
}
