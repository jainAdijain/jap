import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  server: {
    // Source is bind-mounted from the Mac; polling keeps reload working through Docker.
    watch: { usePolling: true },
  },
})
