import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In production nginx serves the built app and proxies /api to the HQ API on 127.0.0.1:8787.
// In development (and the demo) Vite does the same.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8787" } },
  preview: { proxy: { "/api": "http://127.0.0.1:8787" } },
});
