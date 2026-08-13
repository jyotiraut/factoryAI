import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
// Port 3000, not Vite's default 5173: matches the backend's `API_CORS_ORIGINS` default
// (see `.env.example`), so the API's CORS middleware accepts this dev server out of the
// box, with no config change required for the exit criterion "all dashboard data comes
// from the public API" to be exercisable at all in local dev.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
  },
})
