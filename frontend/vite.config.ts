import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    rollupOptions: {
      output: {
        // Vite 8 bundles with rolldown, which only accepts the *function* form
        // of manualChunks (the object form is Rollup-only and throws
        // "manualChunks is not a function"). Group the React runtime into a
        // single long-lived vendor chunk so app-code changes don't invalidate
        // it in the browser cache.
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          const isVendor = /[\\/]node_modules[\\/](react|react-dom|react-router|react-router-dom|scheduler)[\\/]/.test(
            id,
          );
          return isVendor ? "vendor" : undefined;
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
