import { test } from "node:test";
import assert from "node:assert/strict";
import { signRequest } from "../src/sign.js";
import { createHmac } from "node:crypto";

test("signRequest matches HMAC-SHA256(canonical) reference impl", () => {
  const secret = "deadbeef";
  const method = "POST";
  const path = "/v1/experiences";
  const body = '{"hello":"world"}';
  const got = signRequest(secret, method, path, body);

  const canonical = Buffer.concat([
    Buffer.from("POST\n"),
    Buffer.from("/v1/experiences\n"),
    Buffer.from(body, "utf-8"),
  ]);
  const want = createHmac("sha256", secret).update(canonical).digest("hex");
  assert.equal(got, want);
});

test("signRequest is case-normalized on method", () => {
  const secret = "x";
  assert.equal(
    signRequest(secret, "post", "/p", "b"),
    signRequest(secret, "POST", "/p", "b"),
  );
});

test("body changes flip the signature", () => {
  const secret = "x";
  const a = signRequest(secret, "POST", "/p", "alpha");
  const b = signRequest(secret, "POST", "/p", "beta");
  assert.notEqual(a, b);
});
