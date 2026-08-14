import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    globals: false,
    // e2e/ holds Playwright specs (test:e2e) — Vitest's default include
    // pattern would otherwise also pick them up and fail, since they call
    // @playwright/test's test() outside a Playwright run.
    exclude: ['**/node_modules/**', 'e2e/**'],
  },
})
