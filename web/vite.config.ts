import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build output goes straight into the Python package so FastAPI can serve
// it as static files. In dev, /api is proxied to the uvicorn backend.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/hots_helper/web/static",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:7860",
    },
  },
});
