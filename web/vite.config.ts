import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  base: './',
  resolve: { alias: [{ find: /^wavefile$/, replacement: 'wavefile/dist/wavefile.js' }] },
  optimizeDeps: { include: ['wavefile', 'fflate'] },
  plugins: [react()],
})
