/** @type {import('next').NextConfig} */
const PUBLIC_URL = process.env.EXP_UI_PUBLIC_URL?.replace(/\/$/, "") || "";
const envList = (value) =>
  (value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);

let PUBLIC_PATH = "";
if (PUBLIC_URL) {
  try {
    PUBLIC_PATH = new URL(PUBLIC_URL).pathname.replace(/\/$/, "");
  } catch {
    PUBLIC_PATH = "";
  }
}

const serverActionOrigins = [
  ...envList(process.env.EXP_SERVER_ACTION_ALLOWED_ORIGINS),
  "127.0.0.1:3000",
  "localhost:3000",
];
if (PUBLIC_URL) {
  try {
    serverActionOrigins.push(new URL(PUBLIC_URL).host);
  } catch {
    // Ignore malformed public URL; the app will still use local defaults.
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
      allowedOrigins: Array.from(new Set(serverActionOrigins)),
    },
  },
  allowedDevOrigins: envList(process.env.EXP_ALLOWED_DEV_ORIGINS),
};

export default nextConfig;
