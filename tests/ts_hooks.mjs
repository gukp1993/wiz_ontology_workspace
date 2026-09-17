/**Node 原生跑前端 TS 的模块解析钩子：为相对路径的无扩展名导入补 .ts。
 * 用法：node --import ./tests/ts_hooks.mjs <test>.mjs
 * 仅服务测试运行，不参与 Vite/vue-tsc 构建。*/
import { register } from 'node:module'
register(new URL('./ts_resolve_hook.mjs', import.meta.url))
