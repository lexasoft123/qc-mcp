import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'node:path'
export default defineConfig({
  root: __dirname,
  plugins: [react()],
  resolve: { alias: { '@shared': resolve(__dirname, '../src/shared') } },
  build: { outDir: resolve(__dirname, 'dist'), emptyOutDir: true }
})
