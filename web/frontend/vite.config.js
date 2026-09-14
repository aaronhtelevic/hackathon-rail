import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

const API = process.env.RAIL_GUI_API ?? 'http://localhost:5174'

// Dev server proxies /api to the Node server so the browser talks to one origin.
export default defineConfig({
  plugins: [svelte()],
  server: {
    port: Number(process.env.RAIL_GUI_WEB_PORT ?? 5173),
    proxy: {
      '/api': { target: API, changeOrigin: true, ws: false },
    },
  },
  build: { outDir: 'dist', emptyOutDir: true },
})
