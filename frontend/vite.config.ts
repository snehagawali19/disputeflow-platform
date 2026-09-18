/// <reference types="vitest" />
import { defineConfig } from "vite";
import { loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, "..", "");
  const proxyHeaders = { "X-API-Key": env.API_KEY || "disputeflow-dev-api-key-change-in-production" };

  return ({
  plugins: [react()],
  envDir: "..",
  server: {
    port: 3000,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        headers: proxyHeaders,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
      "/ws": {
        target: "ws://localhost:8000",
        ws: true,
        headers: proxyHeaders,
      },
      "/health": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/setupTests.ts",
  },
  });
});
