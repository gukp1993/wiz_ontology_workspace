/**解析钩子实现：'./app/http' → './app/http.ts'（仅当补扩展名后文件存在）。*/
import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

export async function resolve(specifier, context, nextResolve) {
  if (specifier.startsWith('.') && !/\.[cm]?[jt]s$/.test(specifier) && context.parentURL) {
    const base = new URL(specifier, context.parentURL)
    for (const suffix of ['.ts', '/index.ts']) {
      const candidate = new URL(base.href + suffix)
      if (existsSync(fileURLToPath(candidate))) return nextResolve(candidate.href, context)
    }
  }
  return nextResolve(specifier, context)
}
