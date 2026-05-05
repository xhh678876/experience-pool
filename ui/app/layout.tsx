import type { Metadata } from "next";
import "./globals.css";
import AutoRefresh from "@/components/ui/auto-refresh";
import MainNav from "@/components/MainNav";
import UserPanel from "@/components/UserPanel";

export const metadata: Metadata = {
  title: "创智经验池",
  description: "创智内网多 Agent 共享经验池",
};

const criticalWorkbenchCss = `
html.cz-silver,
html.cz-silver body {
  margin: 0;
  min-height: 100%;
  background: #f7f8f6;
  color: #10231f;
  color-scheme: light;
}
html.cz-silver body {
  background-image:
    linear-gradient(180deg, #ffffff 0%, #f3f7f7 48%, #f4f1e7 100%),
    repeating-linear-gradient(90deg, rgba(31, 98, 126, 0.075) 0 1px, transparent 1px 96px);
  font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-size: 14px;
  line-height: 1.5;
}
html.cz-silver * {
  box-sizing: border-box;
  letter-spacing: 0;
}
html.cz-silver a {
  color: inherit;
  text-decoration: none;
}
html.cz-silver svg {
  width: 1rem;
  height: 1rem;
  flex: 0 0 auto;
  vertical-align: -0.15em;
}
html.cz-silver h1,
html.cz-silver h2,
html.cz-silver h3,
html.cz-silver p {
  margin: 0;
}
html.cz-silver header {
  position: sticky;
  top: 0;
  z-index: 40;
  border-bottom: 1px solid rgba(143, 155, 158, 0.46);
  background: rgba(248, 250, 249, 0.94);
  backdrop-filter: blur(14px);
}
html.cz-silver header > div,
html.cz-silver body > main {
  width: min(1600px, calc(100vw - 24px));
  margin: 0 auto;
}
html.cz-silver header > div {
  min-height: 48px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 0;
}
html.cz-silver header nav,
html.cz-silver header nav a,
html.cz-silver header > div > div,
html.cz-silver header > div > div > span,
html.cz-silver header > div > div > a {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
html.cz-silver header nav {
  flex-wrap: wrap;
}
html.cz-silver header nav a,
html.cz-silver header > div > div > span,
html.cz-silver header > div > div > a {
  min-height: 28px;
  border-radius: 6px;
  padding: 4px 8px;
  color: #52625f;
}
html.cz-silver header nav a:first-child {
  color: #0f5f68;
  font-weight: 650;
}
html.cz-silver header nav a:hover,
html.cz-silver header > div > div > a:hover {
  background: rgba(14, 117, 104, 0.11);
  color: #10231f;
}
html.cz-silver body > main {
  padding: 12px 0 18px;
}
html.cz-silver main > div {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
html.cz-silver main section,
html.cz-silver main .rounded-lg {
  border: 1px solid rgba(143, 155, 158, 0.42);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.82);
  overflow: hidden;
}
html.cz-silver main > div > section:first-child > div:first-child,
html.cz-silver main section > div:first-child {
  border-bottom: 1px solid rgba(143, 155, 158, 0.36);
  background:
    linear-gradient(90deg, rgba(255, 255, 255, 0.96), rgba(226, 236, 238, 0.9) 52%, rgba(255, 250, 224, 0.78)),
    rgba(245, 248, 248, 0.94);
}
html.cz-silver main > div > section:first-child > div:first-child {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px;
}
html.cz-silver main > div > section:first-child > div:nth-child(2) {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  background: rgba(143, 155, 158, 0.32);
}
html.cz-silver main > div > section:first-child > div:nth-child(2) > div {
  background: rgba(255, 255, 255, 0.9);
  padding: 12px;
}
html.cz-silver main > div > section:nth-child(2) {
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr) 360px;
  gap: 12px;
  border: 0;
  background: transparent;
}
html.cz-silver aside,
html.cz-silver main > div > section:nth-child(2) > main {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 12px;
}
html.cz-silver aside section,
html.cz-silver main > div > section:nth-child(2) > main section {
  margin: 0;
}
html.cz-silver section > div:first-child {
  min-height: 44px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 12px;
}
html.cz-silver section > div:first-child > div {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
html.cz-silver section > div:first-child h2 {
  color: #10231f;
  font-size: 14px;
  font-weight: 650;
}
html.cz-silver section > div:first-child p,
html.cz-silver .text-muted-foreground {
  color: #52625f;
}
html.cz-silver label {
  display: grid;
  gap: 6px;
  color: #52625f;
  font-size: 12px;
  font-weight: 600;
}
html.cz-silver input,
html.cz-silver select,
html.cz-silver textarea {
  min-height: 36px;
  width: 100%;
  border: 1px solid rgba(143, 155, 158, 0.48);
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.86);
  color: #10231f;
  padding: 6px 10px;
  font: inherit;
}
html.cz-silver .inline-flex {
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
html.cz-silver button:not([class]) {
  min-height: 36px;
  border: 1px solid rgba(14, 117, 104, 0.28);
  border-radius: 6px;
  background: #0f8f88;
  color: #fff;
  padding: 6px 12px;
  font: inherit;
  font-weight: 650;
}
html.cz-silver table {
  width: 100%;
  border-collapse: collapse;
}
html.cz-silver th,
html.cz-silver td {
  border-top: 1px solid rgba(143, 155, 158, 0.32);
  padding: 10px 12px;
  text-align: left;
}
html.cz-silver th {
  color: #52625f;
  background: rgba(226, 236, 238, 0.76);
  font-weight: 650;
}
html.cz-silver footer {
  border: 1px solid rgba(143, 155, 158, 0.42);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.92);
  color: #344541;
  padding: 8px 12px;
}
@media (max-width: 1180px) {
  html.cz-silver main > div > section:nth-child(2) {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 720px) {
  html.cz-silver header > div,
  html.cz-silver main > div > section:first-child > div:first-child {
    align-items: flex-start;
    flex-direction: column;
  }
  html.cz-silver main > div > section:first-child > div:nth-child(2) {
    grid-template-columns: 1fr 1fr;
  }
}
`;

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <head>
        <style
          id="cz-critical-workbench"
          dangerouslySetInnerHTML={{ __html: criticalWorkbenchCss }}
        />
      </head>
      <body
        className="min-h-screen antialiased"
        style={{ background: "#f7f8f6", color: "#10231f" }}
      >
        <header className="sticky top-0 z-40 border-b border-border/70 bg-background/90 backdrop-blur supports-[backdrop-filter]:bg-background/78">
          <div className="mx-auto flex min-h-12 max-w-[1600px] flex-col gap-2 px-3 py-2 lg:h-12 lg:flex-row lg:items-center lg:justify-between lg:py-0">
            <MainNav />
            <div className="flex items-center gap-2">
              <AutoRefresh intervalMs={5000} />
              <UserPanel />
            </div>
          </div>
        </header>
        <main className="mx-auto max-w-[1600px] px-3 py-3">{children}</main>
      </body>
    </html>
  );
}
