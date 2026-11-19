import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // The API is same-origin behind nginx in deployment. In development it runs as a
    // separate process, so proxying keeps the frontend's fetch paths identical in both
    // environments — no environment-dependent base URL, and no CORS preflight in dev.
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
  build: { outDir: "dist", sourcemap: true },
});
