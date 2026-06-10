import type { Metadata } from "next";
import "./globals.css";
import AutoRefresh from "@/components/ui/auto-refresh";
import MainNav from "@/components/MainNav";
import UserPanel from "@/components/UserPanel";
import SiteFooter from "@/components/SiteFooter";

export const metadata: Metadata = {
  title: "经验池 Experience Pool",
  description: "多 Agent 共享经验池",
};

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
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
        <SiteFooter />
      </body>
    </html>
  );
}
