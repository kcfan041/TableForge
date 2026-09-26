import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const coordinator = process.env.TABLEFORGE_WEB_API ?? "http://127.0.0.1:8080";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/tableforge/web/static",
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 800,
    rollupOptions: {
      input: {
        index: fileURLToPath(new URL("./index.html", import.meta.url)),
        local: fileURLToPath(new URL("./local.html", import.meta.url)),
      },
    },
  },
  server: {
    proxy: {
      "/api": { target: coordinator, changeOrigin: false },
    },
  },
});
