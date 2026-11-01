import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const API = "http://localhost:8000";
const paths = ["/health", "/languages", "/documents", "/ingest", "/sources", "/ask", "/explain", "/agent", "/tts", "/stt"];

export default defineConfig({
  plugins: [react()],
  server: { proxy: Object.fromEntries(paths.map((p) => [p, API])) },
});
