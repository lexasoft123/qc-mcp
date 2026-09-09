import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'node:path'
export default defineConfig({
  root: __dirname,
  plugins: [react()],
  resolve: { alias: { '@shared': resolve(__dirname, '../src/shared') } },
  build: {
    outDir: resolve(__dirname, 'dist'),
    emptyOutDir: true,
    rollupOptions: {
      input: {
        main: resolve(__dirname, 'index.html'),
        modes: resolve(__dirname, 'modes.html'),
        keys: resolve(__dirname, 'keys.html'),
        bench: resolve(__dirname, 'bench.html')
      }
    }
  }
})
