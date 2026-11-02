import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Node's process, without pulling in @types/node for one variable.
declare const process: { env: Record<string, string | undefined> }

export default defineConfig({
  plugins: [react()],
  server: {
    // Source is bind-mounted from the Mac; polling keeps reload working through Docker.
    watch: { usePolling: true },
    // The UI calls /api on its own address (H2); in dev Vite passes it to the app.
    proxy: { '/api': process.env.VITE_API_PROXY ?? 'http://localhost:8001' },
  },
})
