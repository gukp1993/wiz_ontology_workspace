// 前端代码规范基线（2026-09-21 起）
// 参照：Vue 官方风格指南（https://vuejs.org/style-guide/，规则分级 A Essential /
//       B Strongly Recommended / C Recommended）
// 工具：eslint + eslint-plugin-vue（github.com/vuejs/eslint-plugin-vue）
//       + @vue/eslint-config-typescript（TypeScript 侧配合既有 vue-tsc 严格检查）
// 策略：启用 flat/recommended（= A+B+C 级）与 TS recommended 的**语义规则**；
// 模板格式化类规则（换行/缩进/空格/自闭合等，等价 Prettier 重排）显式关闭——
// 仓库不引入 Prettier，既有模板排版不重排；与仓库既有风格冲突的条目同样
// 显式关闭并注明原因。新代码遵循 vue-tsc 类型检查 + 本配置语义规则。
import pluginVue from 'eslint-plugin-vue'
import { defineConfigWithVueTs, vueTsConfigs } from '@vue/eslint-config-typescript'

export default defineConfigWithVueTs(
  {
    ignores: ['dist/**', 'node_modules/**'],
  },
  pluginVue.configs['flat/recommended'],
  vueTsConfigs.recommended,
  {
    rules: {
      // --- 模板格式化（Prettier 领域）：仓库不重排既有排版 ---
      'vue/max-attributes-per-line': 'off',
      'vue/html-indent': 'off',
      'vue/singleline-html-element-content-newline': 'off',
      'vue/multiline-html-element-content-newline': 'off',
      'vue/mustache-interpolation-spacing': 'off',
      'vue/html-closing-bracket-spacing': 'off',
      'vue/html-closing-bracket-newline': 'off',
      'vue/html-self-closing': 'off',
      'vue/first-attribute-linebreak': 'off',
      'vue/attributes-order': 'off',
      'vue/attribute-hyphenation': 'off',
      'vue/html-quotes': 'off',
      // --- 仓库既有约定 ---
      // 组件目录即业务域，沿用历史单词命名（现有命名无与原生元素冲突案例）
      'vue/multi-word-component-names': 'off',
      // 单文件组件默认导出是既有约定
      // --- 渐进类型基线 ---
      // 既有代码大量显式 any（渐进类型），类型安全由 vue-tsc 严格检查兜底；
      // 新代码应尽量避免 any，但不作为 lint 错误阻断
      '@typescript-eslint/no-explicit-any': 'off',
      // --- 已知债务（登记于 文档/需求/20260921_仓库架构整理与代码规范/开发计划.md）---
      // vue/no-mutating-props 既有 69 处：属行为级重构，须配合浏览器回归分批处理，
      // 本轮不启用以免大面积破坏已验收交互；后续专项整改后再开启
      'vue/no-mutating-props': 'off',
      // legacyGraph/ 系旧工程整体迁入的 JavaScript 代码（<script setup> 无 lang），
      // 保持 JS 本质不强制 lang="ts"；新代码（src 其余区域）一律 TS
      'vue/block-lang': 'off',
      // 既有 props 无默认值写法（TS 类型即契约），不强制补默认值
      'vue/require-default-prop': 'off',
      // 未使用变量：允许下划线前缀豁免（与后端 Ruff 约定一致）
      '@typescript-eslint/no-unused-vars': ['error', {
        argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrorsIgnorePattern: '^_',
      }],
    },
  },
)
