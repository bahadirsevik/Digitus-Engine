import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  server: {
    port: 3000,
    host: '0.0.0.0',
    proxy: {
      '/health': {
        target: mode === 'docker' ? 'http://app:8000' : 'http://localhost:8000',
        changeOrigin: true,
      },
      '/api': {
        target: mode === 'docker' ? 'http://app:8000' : 'http://localhost:8000',
        changeOrigin: true,
        headers: process.env.API_KEY
          ? { 'X-API-Key': process.env.API_KEY }
          : undefined,
      }
    }
  }
}))
