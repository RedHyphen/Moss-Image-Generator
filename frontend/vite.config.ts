import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiTarget = process.env.MOSS_DEV_API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": apiTarget },
  },
});
