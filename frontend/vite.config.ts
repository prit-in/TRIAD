import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The API base is read from the environment so the same build runs against a local stack or
// a deployed one. It is never hard-coded to localhost.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
    // The source arrives over a bind mount from a Windows host, and inotify events do not
    // cross that boundary. Without polling the dev server silently serves the code it first
    // read, which looks exactly like a change that did not work.
    watch: { usePolling: true, interval: 300 },
  },
});
