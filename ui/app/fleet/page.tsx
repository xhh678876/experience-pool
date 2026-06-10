import { withBase } from "@/lib/base-path";
import FleetLive, { type FleetData } from "./FleetLive";

// claude-fleet 监控面板（监控本 pod 上的 claude-code / codex 会话）。
// 服务端直读 fleet :7878 拿初始数据（无 basePath/CORS 问题）；
// 客户端再走 withBase("/api/fleet/windows") 反代轮询实时刷新。
const FLEET_ORIGIN = process.env.FLEET_ORIGIN ?? "http://127.0.0.1:7878";
const FLEET_ENABLED = ["1", "true", "yes"].includes(
  (process.env.EXP_FLEET_ENABLED ?? "").toLowerCase(),
);

export const dynamic = "force-dynamic";

async function getInitial(): Promise<FleetData> {
  try {
    const r = await fetch(`${FLEET_ORIGIN}/api/windows`, { cache: "no-store" });
    if (!r.ok) return { windows: [], error: `fleet 返回 ${r.status}` };
    return (await r.json()) as FleetData;
  } catch (e) {
    return { windows: [], error: `连不上 claude-fleet（${FLEET_ORIGIN}）：${String(e)}` };
  }
}

export default async function FleetPage() {
  if (!FLEET_ENABLED) {
    return (
      <div className="flex flex-col gap-4 pb-12">
        <section className="rounded-2xl border border-border/60 bg-white/85 px-5 py-6">
          <h1 className="text-sm font-semibold">claude-fleet 监控未启用</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
            这是可选的本机 agent 会话监控入口。部署时设置{" "}
            <code className="font-mono">EXP_FLEET_ENABLED=1</code>
            ，并让 <code className="font-mono">FLEET_ORIGIN</code>
            指向只绑定本机的 claude-fleet 服务后才会启用。
          </p>
        </section>
      </div>
    );
  }
  const initial = await getInitial();
  return <FleetLive initial={initial} pollUrl={withBase("/api/fleet/windows")} />;
}
