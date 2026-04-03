import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  server: {
    port: 3000,
    host: '0.0.0.0',
    proxy: {
      '/api': {
        target: mode === 'docker' ? 'http://app:8000' : 'http://localhost:8000',
        changeOrigin: true,
      }
    }
  }
}))
