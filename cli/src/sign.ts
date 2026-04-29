/**
 * HMAC-SHA256 request signing matching the Python `identity.sign_request` impl:
 *
 *   canonical = METHOD\nPATH\nBODY
 *   signature = hex(hmac_sha256(secret, canonical))
 *
 * The gateway's verify middleware reads X-Agent-Name + X-Signature headers.
 */

import { createHmac } from "node:crypto";

export function signRequest(
  secret: string,
  method: string,
  path: string,
  body: Buffer | string
): string {
  const bodyBuf = typeof body === "string" ? Buffer.from(body, "utf-8") : body;
  const canonical = Buffer.concat([
    Buffer.from(method.toUpperCase() + "\n"),
    Buffer.from(path + "\n"),
    bodyBuf,
  ]);
  return createHmac("sha256", secret).update(canonical).digest("hex");
}
