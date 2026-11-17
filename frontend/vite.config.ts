import path from "node:path";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  // Relative base so the built assets work when served by `openclip serve`
  // from any sub-path. Hash routing (#/) never conflicts with static files.
  base: "./",
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    fs: {
      allow: [".."],
    },
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        // Required for SSE: disable response buffering so event-stream
        // events arrive at the browser in real time.
        configure: (proxy) => {
          proxy.on("proxyReq", (_proxyReq, _req, res) => {
            // Tell nginx/intermediaries not to buffer this response.
            // Vite's proxy (http-proxy) passes through content-type, so
            // EventSource works; this header is belt-and-suspenders.
            res.setHeader("X-Accel-Buffering", "no");
          });
        },
      },
      "/health": {
        target: "http://127.0.0.1:8000",
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.ts",
    // Use mock client in unit/integration tests (no real backend needed)
    env: {
      VITE_API_MODE: "mock",
    },
  },
});
