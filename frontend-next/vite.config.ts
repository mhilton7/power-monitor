import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

const performanceBudgetBytes = 480_000
const frontendVersion = process.env.VITE_BUILD_VERSION ?? '1.0.0-dev'
const frontendCommit = process.env.VITE_RELEASE_COMMIT ?? 'development'

export default defineConfig({
  plugins: [react()],
  define: {
    __SINGLE_HOME_MODE__: JSON.stringify(process.env.VITE_SINGLE_HOME_MODE !== 'false'),
    __PERFORMANCE_BUDGET_BYTES__: JSON.stringify(performanceBudgetBytes),
    __FRONTEND_VERSION__: JSON.stringify(frontendVersion),
    __FRONTEND_COMMIT__: JSON.stringify(frontendCommit),
  },
  server: {
    port: 5190,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
    },
  },
  preview: { port: 4190 },
  build: {
    sourcemap: false,
    target: 'es2022',
    reportCompressedSize: true,
    chunkSizeWarningLimit: Math.ceil(performanceBudgetBytes / 1000),
    rollupOptions: {
      output: {
        manualChunks: {
          react: ['react', 'react-dom'],
          query: ['@tanstack/react-query'],
          charts: ['chart.js', 'react-chartjs-2'],
          icons: ['lucide-react'],
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: './tests/setup.ts',
    exclude: ['e2e/**', 'node_modules/**', 'dist/**'],
    coverage: { reporter: ['text', 'html'] },
  },
})
