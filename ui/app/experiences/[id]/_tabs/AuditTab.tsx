import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { AuditRow } from "@/lib/types";
import { formatDate } from "@/lib/utils";

export function AuditTab({ audits }: { audits: AuditRow[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Audit log</CardTitle>
        <p className="text-xs text-muted-foreground mt-1">
          Most recent first. Includes pipeline stages, reviewer actions, and queued requests.
        </p>
      </CardHeader>
      <CardContent>
        {audits.length === 0 ? (
          <p className="text-sm text-muted-foreground">No audit rows for this target.</p>
        ) : (
          <ul className="space-y-4">
            {audits.map((a) => (
              <li key={a.audit_id} className="border-l-2 pl-4 border-foreground/30">
                <div className="flex items-center gap-3 text-xs">
                  <span className="font-medium">{a.action}</span>
                  <span className="text-muted-foreground">{a.actor}</span>
                  <span className="text-muted-foreground/70 ml-auto">{formatDate(a.created_at)}</span>
                </div>
                {a.payload && a.payload !== "{}" ? (
                  <pre className="mt-2 text-xs font-mono bg-muted rounded p-2 overflow-auto whitespace-pre-wrap break-all">
                    {prettyJson(a.payload)}
                  </pre>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function prettyJson(s: string): string {
  try {
    return JSON.stringify(JSON.parse(s), null, 2);
  } catch {
    return s;
  }
}
