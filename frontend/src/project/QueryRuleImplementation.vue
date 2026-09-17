<script setup lang="ts">
const props = defineProps<{ rule: any }>()
const operators: Record<string, string> = { eq: '等于', gte: '大于等于', lt: '小于' }
function describe(value: any): string {
  if (typeof value !== 'string') return JSON.stringify(value) ?? '未配置'
  const input = value.match(/^\{\{inputs\.(\w+)\}\}$/)
  if (input) return `输入参数 ${input[1]}`
  const ref = value.match(/^\{\{steps\.(\w+)\.(\w+)\}\}$/)
  if (ref) {
    const index = (props.rule.steps || []).findIndex((s: any) => s.id === ref[1])
    return `步骤 ${index + 1} 的输出 ${ref[2]}`
  }
  return value || '未配置'
}
const policyNames: Record<string, string> = {
  lookupMissing: '测点或存储记录缺失', lookupMultiple: '测点或存储记录不唯一',
  emptySeries: '没有采样记录', nullValue: '采样值为空',
  identifiers: '动态表与字段检查', timezone: '时间口径', range: '查询区间',
}
const policyValues: Record<string, string> = {
  error: '报错', empty: '返回空序列', preserve: '保留 null，不转为 0',
  connectionCatalog: '按连接目录校验', project: '使用项目时区',
}
</script>

<template>
<section v-if="rule.mode==='sqlSteps'" class="implementation" aria-label="SQL 步骤">
  <h3>SQL 查询步骤</h3>
  <p class="field-help">每步一段 SELECT 模板；<code>:名称</code> 为输入值，<code>:步骤.列</code> 为前置唯一记录的值，<code v-text="'{{步骤.列}}'"></code> 为动态表名／字段名。已保存的模板配置，尚未执行数据库查询。</p>
  <section v-for="(step, index) in rule.steps" :key="step.id || index" class="implementation-step">
    <h4>步骤 {{ Number(index) + 1 }} · {{ step.name || step.key }} <small class="muted">（{{ step.key }} · {{ step.cardinality === 'one' ? '一条' : '多条' }}）</small></h4>
    <pre class="sql-pre">{{ step.sql }}</pre>
  </section>
  <p><strong>最终返回：</strong>步骤 {{ (rule.steps || []).findIndex((s: any) => s.id === rule.result?.step) + 1 }} 的
    <template v-if="rule.result?.type === 'timeSeries'">{{ rule.result?.timestamp }}（时间）与 {{ rule.result?.value }}（观测值）组成的{{ rule.result?.valueType === 'double' ? '数值' : rule.result?.valueType }}时间序列</template>
    <template v-else>{{ rule.result?.value }}（单值）</template>。</p>
</section>
<section v-else-if="rule.mode==='sqlTemplate'" class="implementation" aria-label="SQL 模板"><h3>SQL 模板</h3><pre style="white-space:pre-wrap;overflow-wrap:anywhere">{{rule.sqlTemplate}}</pre><p class="field-help">已保存的模板配置，尚未执行数据库查询。</p></section>
<section v-else class="implementation" aria-label="规则实现">
  <h3>规则实现</h3>
  <p class="field-help">以下内容来自当前规则配置。动态表和字段由前一步查询结果确定，每个实例可以不同。</p>
  <section v-for="(step, index) in rule.steps" :key="step.id" class="implementation-step">
    <h4>步骤 {{ Number(index) + 1 }} · {{ step.name }}</h4>
    <p><strong>查询表：</strong>{{ describe(step.table) }}</p>
    <div class="implementation-table"><table>
      <thead><tr><th>条件字段</th><th>比较方式</th><th>取值来源（条件同时满足）</th></tr></thead>
      <tbody><tr v-for="(condition, i) in step.where" :key="i">
        <td>{{ describe(condition.field) }}</td><td>{{ operators[condition.op] || condition.op }}</td><td>{{ describe(condition.value) }}</td>
      </tr></tbody>
    </table></div>
    <p><strong>提取结果：</strong></p>
    <ul><li v-for="field in step.select" :key="field.as">{{ describe(field.field) }} → {{ field.as }}</li></ul>
    <p class="field-help">{{ step.cardinality === 'one' ? '预期一条记录，供后续步骤使用。' : '读取多条采样记录。' }}</p>
  </section>
  <p><strong>最终返回：</strong>步骤 {{ (rule.steps || []).findIndex((s: any) => s.id === rule.result?.step) + 1 }} 的
    {{ rule.result?.timestamp }}（时间）与 {{ rule.result?.value }}（观测值），
    {{ rule.result?.order === 'ascending' ? '按时间升序' : rule.result?.order || '未配置排序' }}。</p>
  <ul><li v-for="(value, key) in rule.policies" :key="key">{{ policyNames[key] || key }}：{{ policyValues[String(value)] || value }}</li></ul>
  <p class="field-help">这里展示已定义的查询流程，不代表已执行真实数据库查询。</p>
</section>
</template>

<style scoped>
.implementation{margin:18px 0;padding-top:12px;border-top:1px solid var(--line)}
.implementation-step{margin:12px 0;padding:14px;background:var(--bg);border:1px solid var(--line);border-radius:8px}
h4{margin:0 0 10px}.implementation-table{overflow-x:auto}table{width:100%;text-align:left}th,td{padding:8px;overflow-wrap:anywhere}p,li{overflow-wrap:anywhere}ul{padding-left:22px}
.sql-pre{white-space:pre-wrap;overflow-wrap:anywhere;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;line-height:1.6;margin:0;background:var(--paper);border:1px solid var(--line);border-radius:6px;padding:10px 12px}
</style>
