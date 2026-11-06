import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const backendUrl = process.env.BACKEND_URL ?? 'http://localhost:3000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    allowedHosts: ['transcendence.test'],
    proxy: {
      '/api': {
        target: backendUrl,
        // Appends the client address to X-Forwarded-For, as Nginx does: the
        // login limit counts it (backend/src/app.setup.ts).
        xfwd: true,
      },
    },
  },
})
