import path from 'path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: false,
    proxy: {
      '/v1': { target: 'http://127.0.0.1:8765', changeOrigin: true },
      '/thumbs': { target: 'http://127.0.0.1:8765', changeOrigin: true },
      '/healthz': { target: 'http://127.0.0.1:8765', changeOrigin: true },
      // Embedding server (timetrace-embserver) is a separate local process on
      // 8766. Strip the /emb prefix so /emb/admin/* -> 8766/admin/*.
      '/emb': {
        target: 'http://127.0.0.1:8766',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/emb/, ''),
      },
    },
  },
  build: {
    outDir: '../frontend-dist',
    emptyOutDir: true,
  },
})
