"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import {
  approveAction,
  editCardAction,
  exportJsonAction,
  rejectAction,
  rejudgeAction,
  softDeleteAction,
} from "@/app/_actions/actions";
import type { ExperienceListItem } from "@/lib/types";
import { tryParseJson } from "@/lib/utils";

export function ActionBar({
  id,
  experience,
}: {
  id: string;
  experience: ExperienceListItem;
}) {
  const [open, setOpen] = useState<null | "reject" | "edit">(null);

  return (
    <>
      <div className="fixed bottom-0 left-0 right-0 border-t bg-card/95 backdrop-blur supports-[backdrop-filter]:bg-card/80 z-20">
        <div className="mx-auto max-w-7xl px-6 py-3 flex items-center gap-2 flex-wrap">
          <form action={approveAction}>
            <input type="hidden" name="id" value={id} />
            <Button type="submit" variant="success">
              Approve
            </Button>
          </form>
          <Button
            variant="destructive"
            onClick={() => setOpen(open === "reject" ? null : "reject")}
          >
            Reject
          </Button>
          <Button
            variant="outline"
            onClick={() => setOpen(open === "edit" ? null : "edit")}
          >
            Edit
          </Button>
          <form action={rejudgeAction}>
            <input type="hidden" name="id" value={id} />
            <Button type="submit" variant="warn">
              Re-judge
            </Button>
          </form>
          <form action={exportJsonAction}>
            <input type="hidden" name="id" value={id} />
            <Button type="submit" variant="outline">
              Export JSON
            </Button>
          </form>
          <form action={softDeleteAction} className="ml-auto">
            <input type="hidden" name="id" value={id} />
            <Button type="submit" variant="ghost" className="text-muted-foreground">
              Soft-delete
            </Button>
          </form>
        </div>

        {open === "reject" ? (
          <div className="border-t bg-background">
            <form
              action={rejectAction}
              className="mx-auto max-w-7xl px-6 py-3 flex items-center gap-3"
            >
              <input type="hidden" name="id" value={id} />
              <Input
                name="reason"
                placeholder="Reject reason (recorded in audit log)"
                className="flex-1"
                required
              />
              <Button type="submit" variant="destructive">
                Confirm reject
              </Button>
              <Button type="button" variant="ghost" onClick={() => setOpen(null)}>
                Cancel
              </Button>
            </form>
          </div>
        ) : null}

        {open === "edit" ? (
          <EditForm id={id} experience={experience} onClose={() => setOpen(null)} />
        ) : null}
      </div>
    </>
  );
}

function EditForm({
  id,
  experience,
  onClose,
}: {
  id: string;
  experience: ExperienceListItem;
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
        className="mx-auto max-w-7xl px-6 py-4 space-y-3"
      >
        <input type="hidden" name="id" value={id} />
        <p className="text-xs text-muted-foreground">
          Editing sets review_status=&quot;edited&quot;, resets q_update_count to 0, and queues
          a re-embedding request for the Python sidecar (pending_reembed table).
        </p>

        <div>
          <label className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
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
            <label className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
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
            <label className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
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
          <label className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
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
            <label className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
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
            <label className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
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
            Cancel
          </Button>
          <Button type="submit">Save edit</Button>
        </div>
      </form>
    </div>
  );
}
