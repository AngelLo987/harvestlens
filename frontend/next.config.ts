import type { NextConfig } from "next";

const API = process.env.CORNEA_API ?? "http://127.0.0.1:8000";

const config: NextConfig = {
  // MapLibre does not survive React dev double-mount (style load hangs on the second instance)
  reactStrictMode: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API}/api/:path*` }];
  },
};
export default config;
