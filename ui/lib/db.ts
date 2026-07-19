import Database from "better-sqlite3";
import path from "node:path";
import os from "node:os";
import fs from "node:fs";

let _db: Database.Database | null = null;

function resolveDbPath(): string {
  const fromEnv = process.env.EXP_DB_PATH;
  if (fromEnv && fromEnv.length > 0) {
    return fromEnv.replace(/^~(?=$|\/|\\)/, os.homedir());
  }
  const root = process.env.EXP_ROOT;
  if (root && root.length > 0) {
    return path.join(
      /* turbopackIgnore: true */ root.replace(/^~(?=$|\/|\\)/, os.homedir()),
      "pool.db",
    );
  }
  return path.join(/* turbopackIgnore: true */ os.homedir(), ".experience-pool", "pool.db");
}

export function getDb(): Database.Database {
  if (_db) return _db;
  const dbPath = resolveDbPath();
  if (!fs.existsSync(/* turbopackIgnore: true */ dbPath)) {
    // Create the directory at minimum so better-sqlite3 can open in readwrite mode if requested.
    fs.mkdirSync(/* turbopackIgnore: true */ path.dirname(dbPath), { recursive: true });
  }
  const db = new Database(dbPath);
  db.pragma("journal_mode = WAL");
  db.pragma("foreign_keys = ON");
  // The FastAPI server (port 8080) writes to the same pool.db (push,
  // publish, judge, OPF, …) and some of those transactions hold the
  // writer for >5s (model load, embedding batch). We pick 30s as the UI
  // budget — better to render late than to 500. Reads in WAL mode never
  // block on the writer, so this only affects the (rare) UI-side writes.
  db.pragma("busy_timeout = 30000");
  // Use NORMAL synchronous since WAL is on; it's safe and ~10x faster
  // for the UI's tiny inserts under contention.
  db.pragma("synchronous = NORMAL");

  // Ensure helper tables for the reviewer exist (without touching the Python schema).
  db.exec(`
    CREATE TABLE IF NOT EXISTS pending_reembed (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      experience_id TEXT NOT NULL,
      requested_at TEXT NOT NULL DEFAULT (datetime('now')),
      processed INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS pending_rejudge (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      experience_id TEXT NOT NULL,
      requested_at TEXT NOT NULL DEFAULT (datetime('now')),
      processed INTEGER NOT NULL DEFAULT 0
    );
  `);

  _db = db;
  return db;
}

export function dbPath(): string {
  return resolveDbPath();
}
