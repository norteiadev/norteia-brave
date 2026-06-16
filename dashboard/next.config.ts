import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // BFF-only network egress: the dashboard talks to FastAPI exclusively through
  // its own Route Handlers (app/api/[...path]/route.ts) which hold the service
  // secret server-side. No client-side calls reach FastAPI directly (D-02).
  reactStrictMode: true,
};

export default nextConfig;
