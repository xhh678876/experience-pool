import "server-only";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";

export type TrajectoryRead = {
  exists: boolean;
  path: string | null;
  rawSiblingExists: boolean;
  rawSiblingPath: string | null;
  redactedDiffers: boolean;
  bodyText: string | null;
  rawBodyText: string | null;
  error: string | null;
  toolUsage: Record<string, number>;
};

function extractToolUsage(bodyText: string | null): Record<string, number> {
  if (!bodyText) return {};
  let parsed: unknown;
  try {
    parsed = JSON.parse(bodyText);
  } catch {
    return {};
  }
  let turns: unknown;
  if (Array.isArray(parsed)) turns = parsed;
  else if (parsed && typeof parsed === "object") {
    const obj = parsed as Record<string, unknown>;
    turns = obj.trajectory ?? obj.messages ?? obj.events;
  }
  if (!Array.isArray(turns)) return {};

  const counts: Record<string, number> = {};
  const bump = (name: string | undefined) => {
    const k = (name && String(name).trim()) || "tool";
    counts[k] = (counts[k] ?? 0) + 1;
  };

  for (const turn of turns) {
    if (!turn || typeof turn !== "object") continue;
    const t = turn as Record<string, unknown>;

    const content = t.content;
    if (Array.isArray(content)) {
      for (const block of content) {
        if (!block || typeof block !== "object") continue;
        const b = block as Record<string, unknown>;
        if (b.type === "tool_use") bump(b.name as string | undefined);
      }
    }

    const toolCalls = t.tool_calls;
    if (Array.isArray(toolCalls)) {
      for (const tc of toolCalls) {
        if (!tc || typeof tc !== "object") continue;
        const c = tc as Record<string, unknown>;
        const fn = (c.function ?? {}) as Record<string, unknown>;
        bump((fn.name as string | undefined) ?? (c.name as string | undefined));
      }
    }
  }
  return counts;
}

function expandHome(p: string): string {
  return p.replace(/^~(?=$|\/|\\)/, os.homedir());
}

function trustedTrajectoryRoot(): string {
  const explicit = process.env.EXP_TRAJECTORIES_DIR;
  if (explicit) return path.resolve(/* turbopackIgnore: true */ expandHome(explicit));
  const dbPath = process.env.EXP_DB_PATH;
  if (dbPath) return path.resolve(/* turbopackIgnore: true */ path.dirname(expandHome(dbPath)), "trajectories");
  const root = process.env.EXP_ROOT;
  if (root) return path.resolve(/* turbopackIgnore: true */ expandHome(root), "trajectories");
  return path.join(/* turbopackIgnore: true */ os.homedir(), ".experience-pool", "trajectories");
}

export async function readTrajectory(
  trajectoryPath: string | null,
  options: { includeRaw?: boolean; exposePath?: boolean } = {},
): Promise<TrajectoryRead> {
  if (!trajectoryPath) {
    return {
      exists: false,
      path: null,
      rawSiblingExists: false,
      rawSiblingPath: null,
      redactedDiffers: false,
      bodyText: null,
      rawBodyText: null,
      error: null,
      toolUsage: {},
    };
  }
  const resolved = path.isAbsolute(trajectoryPath)
    ? expandHome(trajectoryPath)
    : path.resolve(/* turbopackIgnore: true */ expandHome(trajectoryPath));
  const relative = path.relative(trustedTrajectoryRoot(), resolved);
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    return {
      exists: false,
      path: options.exposePath ? resolved : null,
      rawSiblingExists: false,
      rawSiblingPath: null,
      redactedDiffers: false,
      bodyText: null,
      rawBodyText: null,
      error: "trajectory path is outside the configured storage root",
      toolUsage: {},
    };
  }

  let bodyText: string | null = null;
  try {
    const raw = await fs.readFile(/* turbopackIgnore: true */ resolved, "utf-8");
    try {
      bodyText = JSON.stringify(JSON.parse(raw), null, 2);
    } catch {
      bodyText = raw;
    }
  } catch (err) {
    return {
      exists: false,
      path: options.exposePath ? resolved : null,
      rawSiblingExists: false,
      rawSiblingPath: null,
      redactedDiffers: false,
      bodyText: null,
      rawBodyText: null,
      error: err instanceof Error ? err.message : String(err),
      toolUsage: {},
    };
  }

  const dir = path.dirname(/* turbopackIgnore: true */ resolved);
  const base = path.basename(resolved);
  // Sibling rule: same name with `.raw.json` extension swapped in. We look for
  // `<base-without-ext>.raw.json`.
  const stem = base.replace(/\.json$/, "");
  const rawSiblingPath = path.join(/* turbopackIgnore: true */ dir, `${stem}.raw.json`);

  let rawBodyText: string | null = null;
  let rawSiblingExists = false;
  if (options.includeRaw) {
    try {
      const stat = await fs.stat(/* turbopackIgnore: true */ rawSiblingPath);
      if (stat.isFile()) {
        const raw = await fs.readFile(/* turbopackIgnore: true */ rawSiblingPath, "utf-8");
        rawSiblingExists = true;
        try {
          rawBodyText = JSON.stringify(JSON.parse(raw), null, 2);
        } catch {
          rawBodyText = raw;
        }
      }
    } catch {
      // sibling missing is the common case; ignore.
    }
  }

  const redactedDiffers = rawSiblingExists && rawBodyText !== null && rawBodyText !== bodyText;

  return {
    exists: true,
    path: options.exposePath ? resolved : null,
    rawSiblingExists,
    rawSiblingPath: rawSiblingExists && options.exposePath ? rawSiblingPath : null,
    redactedDiffers,
    bodyText,
    rawBodyText,
    error: null,
    toolUsage: extractToolUsage(bodyText),
  };
}
