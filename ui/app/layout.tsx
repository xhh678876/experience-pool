import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import { getReviewerName } from "@/lib/auth";

export const metadata: Metadata = {
  title: "Experience Pool Reviewer",
  description: "Review extracted experiences, q-values, and credit-assignment lineage.",
};

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const reviewer = await getReviewerName();
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-screen antialiased">
        <header className="border-b bg-card sticky top-0 z-30 backdrop-blur supports-[backdrop-filter]:bg-card/85">
          <div className="mx-auto max-w-7xl px-6 h-14 flex items-center justify-between">
            <nav className="flex items-center gap-6">
              <Link href="/" className="font-semibold tracking-tight">
                Experience Pool
              </Link>
              <Link
                href="/"
                className="text-sm text-muted-foreground hover:text-foreground"
              >
                Dashboard
              </Link>
              <Link
                href="/experiences"
                className="text-sm text-muted-foreground hover:text-foreground"
              >
                Experiences
              </Link>
              <Link
                href="/skills"
                className="text-sm text-muted-foreground hover:text-foreground"
              >
                Skills
              </Link>
            </nav>
            <div className="flex items-center gap-3 text-sm">
              <span className="text-muted-foreground">Reviewer:</span>
              <span className="font-medium">{reviewer}</span>
              <Link
                href="/login"
                className="text-muted-foreground hover:text-foreground underline-offset-4 hover:underline"
              >
                change
              </Link>
            </div>
          </div>
        </header>
        <main className="mx-auto max-w-7xl px-6 py-8">{children}</main>
      </body>
    </html>
  );
}
