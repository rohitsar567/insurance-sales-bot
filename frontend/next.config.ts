import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Static export — the built frontend is served by the FastAPI backend
  // alongside the /api/* routes on a single port. This lets us deploy
  // everything to a single Hugging Face Space (Docker).
  output: "export",
  // Disable next/image optimization in static export (no Node runtime serving it)
  images: { unoptimized: true },
  // Trailing slashes match the way FastAPI's StaticFiles serves directories
  trailingSlash: true,
};

export default nextConfig;
