/**
 * HTTP client for the FastAPI gateway. Auto-signs requests when a credential is
 * available; falls back to anonymous (X-Agent-Name only) for endpoints that
 * accept it.
 */

import { signRequest, type Credential } from "./index-types.js";

export interface Endpoint {
  baseUrl: string;
}

export class GatewayClient {
  private baseUrl: string;
  private cred: Credential | null;

  constructor(opts: { baseUrl: string; cred: Credential | null }) {
    this.baseUrl = opts.baseUrl.replace(/\/$/, "");
    this.cred = opts.cred;
  }

  private buildHeaders(method: string, path: string, body: string): Record<string, string> {
    const h: Record<string, string> = {
      "content-type": "application/json",
    };
    if (this.cred) {
      h["x-agent-name"] = this.cred.agent_name;
      h["x-signature"] = signRequest(this.cred.secret, method, path, body);
    }
    return h;
  }

  async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const bodyStr = body === undefined ? "" : JSON.stringify(body);
    const url = this.baseUrl + path;
    const res = await fetch(url, {
      method,
      headers: this.buildHeaders(method, path, bodyStr),
      body: method === "GET" ? undefined : bodyStr,
    });
    const text = await res.text();
    if (!res.ok) {
      throw new Error(
        `gateway ${method} ${path} -> ${res.status}: ${text.slice(0, 500)}`
      );
    }
    if (!text) return undefined as T;
    try {
      return JSON.parse(text) as T;
    } catch {
      throw new Error(`non-json response from ${path}: ${text.slice(0, 200)}`);
    }
  }

  async getStream<T>(path: string): Promise<T> {
    return this.request<T>("GET", path);
  }
}
