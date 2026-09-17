import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
// 产物文件名保留内容 hash：构建后文件名变化，浏览器必然加载新版本（服务端另有协商缓存头）。
//
// 开发代理（仅 `npm run dev` 使用；生产由服务端直接托管 dist）：
// 服务端 Origin 白名单只接受「服务自身端口」的 127.0.0.1/localhost 来源（安全边界不变）。
// 开发时页面在 vite 端口上，浏览器发来的 Origin 不是服务自身端口，POST 会被 403「请求来源不允许」。
// 这里把转发请求的 Origin 改写为后端自身地址，使白名单继续生效——不放宽服务端校验，
// 也不扩展远程部署支持；生产环境不经过该代理。
const BACKEND = 'http://127.0.0.1:18765'
export default defineConfig({
  plugins: [vue()],
  base: '/',
  build: { outDir: 'dist' },
  server: {
    host: '127.0.0.1',
    proxy: {
      '/api': {
        target: BACKEND,
        configure(proxy) {
          proxy.on('proxyReq', proxyReq => { proxyReq.setHeader('Origin', BACKEND) })
        },
      },
    },
  },
})
