import { NextRequest, NextResponse } from "next/server";

const DEFAULT_REVIEWER = process.env.EXP_DEFAULT_REVIEWER ?? "alice";

export function middleware(req: NextRequest) {
  if (req.nextUrl.pathname.startsWith("/login")) {
    return NextResponse.redirect(new URL("/", req.url));
  }
  const res = NextResponse.next();
  if (!req.cookies.get("X-Reviewer-Name")) {
    res.cookies.set("X-Reviewer-Name", DEFAULT_REVIEWER, {
      httpOnly: false,
      sameSite: "lax",
      path: "/",
      maxAge: 60 * 60 * 24 * 30,
    });
  }
  return res;
}

export const config = {
  matcher: ["/((?!_next/|api/|favicon.ico).*)"],
};
