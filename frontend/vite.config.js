import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
// 产物文件名保留内容 hash：构建后文件名变化，浏览器必然加载新版本（服务端另有协商缓存头）。
export default defineConfig({
  plugins: [vue()],
  base: '/',
  build: { outDir: 'dist' },
  server: { host: '127.0.0.1', proxy: { '/api': 'http://127.0.0.1:18765' } },
})
