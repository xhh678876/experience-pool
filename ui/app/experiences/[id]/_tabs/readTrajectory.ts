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
};

function expandHome(p: string): string {
  return p.replace(/^~(?=$|\/|\\)/, os.homedir());
}

export async function readTrajectory(
  trajectoryPath: string | null,
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
    };
  }
  const resolved = path.isAbsolute(trajectoryPath)
    ? expandHome(trajectoryPath)
    : path.resolve(expandHome(trajectoryPath));

  let bodyText: string | null = null;
  try {
    const raw = await fs.readFile(resolved, "utf-8");
    try {
      bodyText = JSON.stringify(JSON.parse(raw), null, 2);
    } catch {
      bodyText = raw;
    }
  } catch (err) {
    return {
      exists: false,
      path: resolved,
      rawSiblingExists: false,
      rawSiblingPath: null,
      redactedDiffers: false,
      bodyText: null,
      rawBodyText: null,
      error: err instanceof Error ? err.message : String(err),
    };
  }

  const dir = path.dirname(resolved);
  const base = path.basename(resolved);
  // Sibling rule: same name with `.raw.json` extension swapped in. We look for
  // `<base-without-ext>.raw.json`.
  const stem = base.replace(/\.json$/, "");
  const rawSiblingPath = path.join(dir, `${stem}.raw.json`);

  let rawBodyText: string | null = null;
  let rawSiblingExists = false;
  try {
    const stat = await fs.stat(rawSiblingPath);
    if (stat.isFile()) {
      const raw = await fs.readFile(rawSiblingPath, "utf-8");
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

  const redactedDiffers = rawSiblingExists && rawBodyText !== null && rawBodyText !== bodyText;

  return {
    exists: true,
    path: resolved,
    rawSiblingExists,
    rawSiblingPath: rawSiblingExists ? rawSiblingPath : null,
    redactedDiffers,
    bodyText,
    rawBodyText,
    error: null,
  };
}
