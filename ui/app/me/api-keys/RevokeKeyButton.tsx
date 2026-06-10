"use client";

import { useActionState } from "react";
import { useFormStatus } from "react-dom";
import { Button } from "@/components/ui/button";
import { revokeApiKeyAction, type RevokeKeyFormState } from "./actions";

export default function RevokeKeyButton({ keyId, keyName }: { keyId: string; keyName: string }) {
  const [state, formAction] = useActionState<RevokeKeyFormState | undefined, FormData>(
    revokeApiKeyAction,
    undefined,
  );

  return (
    <form
      action={formAction}
      onSubmit={(e) => {
        if (!window.confirm(`确认撤销 key "${keyName}"？撤销后用这把 key 的请求会立即被拒。`)) {
          e.preventDefault();
        }
      }}
    >
      <input type="hidden" name="key_id" value={keyId} />
      <SubmitBtn />
      {state && !state.ok ? (
        <span className="ml-2 text-xs text-rose-700">
          {state.message ?? `HTTP ${state.status}`}
        </span>
      ) : null}
    </form>
  );
}

function SubmitBtn() {
  const { pending } = useFormStatus();
  return (
    <Button type="submit" size="sm" variant="destructive" disabled={pending}>
      {pending ? "撤销中..." : "撤销"}
    </Button>
  );
}
