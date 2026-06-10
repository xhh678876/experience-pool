function clean(value: string): string {
  return value.replace(/\/$/, "");
}

function proxyBaseFromUi(ui: string, targetPort: number): string | null {
  const m = clean(ui).match(/^(.*)\/proxy\/\d+$/);
  if (!m) return null;
  return `${m[1]}/proxy/${targetPort}`;
}

export function publicUiBase(): string {
  const explicit = process.env.EXP_UI_PUBLIC_URL;
  if (explicit) return clean(explicit);

  return "http://127.0.0.1:3000";
}

export function publicGatewayBase(): string {
  const explicit =
    process.env.EXP_PUBLIC_BASE_URL ??
    process.env.EXP_PUBLIC_API_BASE ??
    process.env.EXP_BIND_BASE_URL;
  if (explicit) return clean(explicit);

  const ui = process.env.EXP_UI_PUBLIC_URL;
  if (ui) {
    const fromProxy = proxyBaseFromUi(ui, 3080);
    if (fromProxy) return fromProxy;
  }

  return "http://127.0.0.1:8080";
}
