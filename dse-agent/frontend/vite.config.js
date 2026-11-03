import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发时：Vite dev server 把 API 请求代理到 FastAPI（避免跨域）
// 生产时：npm run build 产出 dist/，由 FastAPI StaticFiles 托管
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/auth': 'http://localhost:8000',
      '/sessions': 'http://localhost:8000',
      '/chat': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
