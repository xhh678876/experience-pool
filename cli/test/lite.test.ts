import { test } from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import os from "node:os";
import { credentialsDir } from "../src/config.js";
import { prepareLocalRuleBased } from "../src/lite.js";

function withEnv(vars: Record<string, string | undefined>, fn: () => void) {
  const old: Record<string, string | undefined> = {};
  for (const key of Object.keys(vars)) old[key] = process.env[key];
  try {
    for (const [key, value] of Object.entries(vars)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    fn();
  } finally {
    for (const [key, value] of Object.entries(old)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
}

test("credentialsDir follows EXP_ROOT when explicit dir is absent", () => {
  withEnv(
    { EXP_CREDENTIALS_DIR: undefined, EXP_ROOT: "/tmp/exp-mvp" },
    () => {
      assert.equal(credentialsDir(), path.join("/tmp/exp-mvp", "credentials"));
    },
  );
});

test("credentialsDir lets EXP_CREDENTIALS_DIR override EXP_ROOT", () => {
  withEnv(
    { EXP_CREDENTIALS_DIR: "/tmp/exp-creds", EXP_ROOT: "/tmp/exp-mvp" },
    () => {
      assert.equal(credentialsDir(), "/tmp/exp-creds");
    },
  );
});

test("credentialsDir expands home for local smoke tests", () => {
  withEnv({ EXP_CREDENTIALS_DIR: "~/exp-creds", EXP_ROOT: undefined }, () => {
    assert.equal(credentialsDir(), path.join(os.homedir(), "exp-creds"));
  });
});

test("prepareLocalRuleBased redacts and produces the MVP card shape", () => {
  const card = prepareLocalRuleBased(
    [
      {
        role: "user",
        content: "Find top regions. Contact alice@example.com and use AKIAIOSFODNN7EXAMPLE.",
      },
      { role: "assistant", content: "Inspect columns, group by region, sum revenue." },
      { role: "assistant", content: "APAC, EMEA, AMER are top." },
    ],
    {
      task_type: "csv_analysis",
      source_model: "claude-mvp",
      sensitivity: "low",
      acl: "public",
      tags: ["mvp"],
    },
  );

  assert.equal(card.task_type, "csv_analysis");
  assert.equal(card.acl, "public");
  assert.equal(card.steps.length, 2);
  assert.match(card.query, /<EMAIL>/);
  assert.match(card.query, /<KEY>/);
  assert.equal(card.redactions.email, 1);
  assert.equal(card.redactions.aws_access_key, 1);
});
