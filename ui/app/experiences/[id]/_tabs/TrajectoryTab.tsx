"use client";

import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import type { TrajectoryRead } from "./readTrajectory";

export function TrajectoryTab({
  trajectory,
  path,
}: {
  trajectory: TrajectoryRead;
  path: string | null;
}) {
  const [showRaw, setShowRaw] = useState(false);

  if (!trajectory.exists) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Trajectory</CardTitle>
        </CardHeader>
        <CardContent>
          {!path ? (
            <p className="text-sm text-muted-foreground">
              This experience has no trajectory_path on file.
            </p>
          ) : (
            <>
              <p className="text-sm text-muted-foreground">
                Could not read trajectory at:
              </p>
              <pre className="text-xs font-mono mt-2 p-3 bg-muted rounded">{path}</pre>
              {trajectory.error ? (
                <p className="text-sm text-red-700 dark:text-red-300 mt-2">{trajectory.error}</p>
              ) : null}
            </>
          )}
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      {trajectory.redactedDiffers ? (
        <div className="rounded-md border border-amber-300 bg-amber-50 text-amber-900 dark:bg-amber-950 dark:text-amber-200 dark:border-amber-800 p-3 flex items-center justify-between text-sm">
          <span>
            Some content was redacted compared to the raw trajectory on disk.
          </span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => setShowRaw((v) => !v)}
          >
            {showRaw ? "Show sanitized" : "Show raw side-by-side"}
          </Button>
        </div>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle className="font-mono text-xs">{trajectory.path}</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {showRaw && trajectory.rawBodyText ? (
            <div className="grid grid-cols-2 divide-x">
              <pre className="text-xs font-mono p-4 max-h-[70vh] overflow-auto whitespace-pre-wrap break-all">
                {trajectory.bodyText}
              </pre>
              <pre className="text-xs font-mono p-4 max-h-[70vh] overflow-auto whitespace-pre-wrap break-all bg-muted/30">
                {trajectory.rawBodyText}
              </pre>
            </div>
          ) : (
            <pre className="text-xs font-mono p-4 max-h-[70vh] overflow-auto whitespace-pre-wrap break-all">
              {trajectory.bodyText}
            </pre>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
