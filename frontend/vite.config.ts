import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiTarget = (process.env.VITE_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

export default defineConfig({
  plugins: [react()],
  define: {
    CESIUM_BASE_URL: JSON.stringify("/node_modules/cesium/Build/Cesium"),
  },
  server: {
    host: "127.0.0.1",
    proxy: {
      "/api": {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
});
