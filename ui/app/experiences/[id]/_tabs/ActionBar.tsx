"use client";

import type { ComponentProps } from "react";
import { useState } from "react";
import { useFormStatus } from "react-dom";
import { Button } from "@/components/ui/button";
import { buttonVariants } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import {
  approveAction,
  editCardAction,
  rejectAction,
  rejudgeAction,
  softDeleteAction,
} from "@/app/_actions/actions";
import type { ExperienceListItem } from "@/lib/types";
import { cn, tryParseJson } from "@/lib/utils";
import { withBase } from "@/lib/base-path";

export function ActionBar({
  id,
  experience,
  returnTo,
}: {
  id: string;
  experience: ExperienceListItem;
  returnTo: string;
}) {
  const [open, setOpen] = useState<null | "reject" | "edit">(null);

  return (
    <>
      <div className="fixed bottom-0 left-0 right-0 border-t bg-card/95 backdrop-blur supports-[backdrop-filter]:bg-card/80 z-20">
        <div className="mx-auto max-w-[1600px] px-3 py-3 flex items-center gap-2 flex-wrap">
          <form action={approveAction}>
            <input type="hidden" name="id" value={id} />
            <input type="hidden" name="returnTo" value={returnTo} />
            <SubmitButton variant="success" pendingLabel="通过中...">
              通过
            </SubmitButton>
          </form>
          <Button
            type="button"
            variant="destructive"
            onClick={() => setOpen(open === "reject" ? null : "reject")}
          >
            拒绝
          </Button>
          <Button
            type="button"
            variant="outline"
            onClick={() => setOpen(open === "edit" ? null : "edit")}
          >
            编辑
          </Button>
          <form action={rejudgeAction}>
            <input type="hidden" name="id" value={id} />
            <input type="hidden" name="returnTo" value={returnTo} />
            <SubmitButton variant="warn" pendingLabel="入队中...">
              重判
            </SubmitButton>
          </form>
          <a
            href={withBase(`/api/export/${encodeURIComponent(id)}`)}
            className={cn(buttonVariants({ variant: "outline" }), "h-9")}
          >
            导出 JSON
          </a>
          <form action={softDeleteAction} className="ml-auto">
            <input type="hidden" name="id" value={id} />
            <input type="hidden" name="returnTo" value={returnTo} />
            <SubmitButton
              variant="ghost"
              className="text-muted-foreground"
              pendingLabel="删除中..."
              onClick={(event) => {
                if (!window.confirm("确认软删除这条经验？会追加 soft_deleted 标签并置为 rejected。")) {
                  event.preventDefault();
                }
              }}
            >
              软删除
            </SubmitButton>
          </form>
        </div>

        {open === "reject" ? (
          <div className="border-t bg-background">
            <form
              action={rejectAction}
              className="mx-auto max-w-[1600px] px-3 py-3 flex items-center gap-3"
            >
              <input type="hidden" name="id" value={id} />
              <input type="hidden" name="returnTo" value={returnTo} />
              <Input
                name="reason"
                placeholder="拒绝原因，会写入审计日志"
                className="flex-1"
                required
              />
              <SubmitButton variant="destructive" pendingLabel="提交中...">
                确认拒绝
              </SubmitButton>
              <Button type="button" variant="ghost" onClick={() => setOpen(null)}>
                取消
              </Button>
            </form>
          </div>
        ) : null}

        {open === "edit" ? (
          <EditForm
            id={id}
            experience={experience}
            returnTo={returnTo}
            onClose={() => setOpen(null)}
          />
        ) : null}
      </div>
    </>
  );
}

function SubmitButton({
  children,
  pendingLabel,
  ...props
}: ComponentProps<typeof Button> & {
  pendingLabel: string;
}) {
  const { pending } = useFormStatus();
  return (
    <Button {...props} type="submit" disabled={pending || props.disabled}>
      {pending ? pendingLabel : children}
    </Button>
  );
}

function EditForm({
  id,
  experience,
  returnTo,
  onClose,
}: {
  id: string;
  experience: ExperienceListItem;
  returnTo: string;
  onClose: () => void;
}) {
  function jsonField(name: string): string {
    const v = experience[name as keyof ExperienceListItem];
    if (typeof v !== "string" || v.length === 0) return "";
    const parsed = tryParseJson(v);
    if (parsed == null) return v;
    return JSON.stringify(parsed, null, 2);
  }

  return (
    <div className="border-t bg-background max-h-[70vh] overflow-auto">
      <form
        action={editCardAction}
        className="mx-auto max-w-[1600px] px-3 py-4 space-y-3"
      >
        <input type="hidden" name="id" value={id} />
        <input type="hidden" name="returnTo" value={returnTo} />
        <p className="text-xs text-muted-foreground">
          保存后会把 review_status 设为 edited，重置 q_update_count，并向 pending_reembed
          写入重新 embedding 请求。
        </p>

        <div>
          <label className="text-xs font-medium text-muted-foreground">
            intent_text
          </label>
          <Textarea
            name="intent_text"
            defaultValue={experience.intent_text ?? ""}
            rows={2}
          />
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          <div>
            <label className="text-xs font-medium text-muted-foreground">
              preconditions (JSON)
            </label>
            <Textarea
              name="preconditions"
              defaultValue={jsonField("preconditions")}
              rows={4}
              className="font-mono text-xs"
            />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">
              tool_capabilities (JSON)
            </label>
            <Textarea
              name="tool_capabilities"
              defaultValue={jsonField("tool_capabilities")}
              rows={4}
              className="font-mono text-xs"
            />
          </div>
        </div>

        <div>
          <label className="text-xs font-medium text-muted-foreground">
            script_steps (JSON)
          </label>
          <Textarea
            name="script_steps"
            defaultValue={jsonField("script_steps")}
            rows={6}
            className="font-mono text-xs"
          />
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          <div>
            <label className="text-xs font-medium text-muted-foreground">
              key_decisions (JSON)
            </label>
            <Textarea
              name="key_decisions"
              defaultValue={jsonField("key_decisions")}
              rows={4}
              className="font-mono text-xs"
            />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">
              pitfalls (JSON)
            </label>
            <Textarea
              name="pitfalls"
              defaultValue={jsonField("pitfalls")}
              rows={4}
              className="font-mono text-xs"
            />
          </div>
        </div>

        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            取消
          </Button>
          <SubmitButton pendingLabel="保存中...">保存编辑</SubmitButton>
        </div>
      </form>
    </div>
  );
}
