/** @type {import('next').NextConfig} */
const PUBLIC_URL = process.env.EXP_UI_PUBLIC_URL?.replace(/\/$/, "") || "";
let PUBLIC_PATH = "";
if (PUBLIC_URL) {
  try {
    PUBLIC_PATH = new URL(PUBLIC_URL).pathname.replace(/\/$/, "");
  } catch {
    PUBLIC_PATH = "";
  }
}

const nextConfig = {
  serverExternalPackages: ["better-sqlite3"],
  ...(PUBLIC_URL ? { assetPrefix: PUBLIC_URL } : {}),
  // dev server uses .next; one-shot type-checks / production builds
  // should set NEXT_DIST_DIR to a separate dir so they don't clobber
  // the dev cache (which causes "Cannot find module './611.js'" 500s).
  distDir: process.env.NEXT_DIST_DIR || ".next",
  env: {
    NEXT_PUBLIC_UI_BASE: PUBLIC_PATH,
  },
  experimental: {
    serverActions: {
      bodySizeLimit: "2mb",
      allowedOrigins: ["nat2-notebook-inspire.sii.edu.cn", "127.0.0.1:3002", "localhost:3002"],
    },
  },
  allowedDevOrigins: ["nat2-notebook-inspire.sii.edu.cn"],
};

export default nextConfig;
