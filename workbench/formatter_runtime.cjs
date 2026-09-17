// Pure display formatting. The launcher restricts permissions, environment and runtime.
// 历史分支（code、string upper/lower/trim/mask/truncate、array count/json、struct json/compact、
// time iso/short、number notation compact/engineering 与 min/max 精度）必须原样可执行；
// 新配置键见 文档/显示格式优化需求_v1_20260915.md 第 6 节。
const vm = require('node:vm');

const EMPTY = c => c.emptyText ?? '—';
const scalarText = (v, c) => v === null || v === undefined ? EMPTY(c) : typeof v === 'object' ? JSON.stringify(v) : String(v);

function fmtNumber(value, c) {
 let v = value;
 if (typeof v === 'string') { if (v.trim() === '') throw Error('请输入有效数值'); v = Number(v); }
 if (typeof v !== 'number' || !Number.isFinite(v)) throw Error('请输入有效数值');
 const opts = { useGrouping: c.grouping ?? true, notation: c.style === 'scientific' ? 'scientific' : (c.notation || 'standard') };
 if (c.decimals !== undefined && c.decimals !== null) {
  if (!Number.isInteger(c.decimals) || c.decimals < 0 || c.decimals > 20) throw Error('小数位数须为0—20的整数');
  opts.minimumFractionDigits = opts.maximumFractionDigits = c.decimals; // 固定小数位，优先于旧 min/max
 } else { opts.minimumFractionDigits = c.minDecimals ?? 0; opts.maximumFractionDigits = c.maxDecimals ?? 2 }
 if (c.style === 'currency') { opts.style = 'currency'; opts.currency = c.currency || 'CNY' }
 if (c.style === 'percent') opts.style = 'percent';
 const out = new Intl.NumberFormat('zh-CN', opts).format(c.style === 'percent' && c.percentInput === 'hundred' ? v / 100 : v);
 return (c.prefix || '') + out + (c.suffix || ''); // 前后缀仅显示拼接，不做换算
}

function fmtString(value, c) {
 let out = String(value);
 if (c.style === 'template') return String(c.template ?? '').split('{value}').join(out); // 仅替换 {value}，其余原样
 if (c.style === 'mapping') { // 精确匹配，未匹配保留原值；不执行代码
  if (Array.isArray(c.mappings)) for (const m of c.mappings) { if (m && typeof m === 'object' && m.from === out) return String(m.to) }
  return out;
 }
 if (c.style === 'upper') out = out.toUpperCase();
 if (c.style === 'lower') out = out.toLowerCase();
 if (c.style === 'trim') out = out.trim();
 if (c.style === 'mask') out = out.length <= 4 ? '*'.repeat(out.length) : out.slice(0, 2) + '*'.repeat(out.length - 4) + out.slice(-2);
 if (c.style === 'truncate') out = out.length > (c.length ?? 20) ? out.slice(0, c.length ?? 20) + '…' : out;
 return (c.prefix || '') + out + (c.suffix || '');
}

function fmtBoolean(value, c) {
 let v = value;
 if (v === 'true' || v === 'false') v = v === 'true';
 if (typeof v !== 'boolean') throw Error('请输入 true 或 false');
 return v ? (c.trueText ?? '是') : (c.falseText ?? '否'); // default 与 custom 同参数
}

function fmtDate(value, c) { // 纯日期：无时区转换，禁止补时分秒
 if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) throw Error('纯日期须为 YYYY-MM-DD 文本');
 const [ys, ms, ds] = value.split('-'), d = new Date(Date.UTC(+ys, +ms - 1, +ds));
 if (d.getUTCFullYear() !== +ys || d.getUTCMonth() !== +ms - 1 || d.getUTCDate() !== +ds) throw Error('日期不存在');
 if (c.style === 'custom') {
  const pattern = c.pattern || 'YYYY-MM-DD';
  if (/HH|mm|ss/.test(pattern)) throw Error('纯日期不能补充时分秒');
  return pattern.replace(/YYYY|MM|DD/g, t => ({ YYYY: ys, MM: ms, DD: ds }[t]));
 }
 const p = c.precision || 'day';
 if (!{ year: 1, month: 1, day: 1 }[p]) throw Error('日期精度应为 year、month 或 day');
 return p === 'year' ? ys : p === 'month' ? `${ys}-${ms}` : `${ys}-${ms}-${ds}`;
}

function fmtTime(value, c) { // 时间戳：原值须含时区；纯日期原值保留旧规则
 if (typeof value !== 'string') throw Error('请输入 ISO 时间文本');
 const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(value), style = c.style || 'date';
 if (dateOnly && !['date', 'custom'].includes(style)) throw Error('原值只有日期，不能显示真实时分秒');
 if (dateOnly && style === 'custom' && /HH|mm|ss/.test(c.pattern || '')) throw Error('原值只有日期，不能补充时分秒');
 if (!dateOnly && !/(Z|[+-]\d{2}:?\d{2})$/.test(value)) throw Error('日期时间需包含时区，如 2026-09-10T14:30:00+08:00');
 const d = new Date(value); if (!Number.isFinite(d.getTime())) throw Error('时间格式无效');
 if (dateOnly && d.toISOString().slice(0, 10) !== value) throw Error('日期不存在');
 if (style === 'iso') return d.toISOString();
 if (style === 'relative') {
  const seconds = Math.round((d.getTime() - Date.now()) / 1000), a = Math.abs(seconds), unit = a < 60 ? 'second' : a < 3600 ? 'minute' : a < 86400 ? 'hour' : 'day';
  return new Intl.RelativeTimeFormat('zh-CN', { numeric: 'auto' }).format(Math.round(seconds / ({ second: 1, minute: 60, hour: 3600, day: 86400 }[unit])), unit);
 }
 const tz = dateOnly ? 'UTC' : c.timezone || 'Asia/Shanghai';
 let parts;
 try { parts = Object.fromEntries(new Intl.DateTimeFormat('en-GB', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }).formatToParts(d).map(p => [p.type, p.value])) }
 catch { throw Error(`时区无效：${tz}`) }
 let pattern;
 if (style === 'custom') pattern = c.pattern || 'YYYY-MM-DD';
 else if (style === 'date') { pattern = { year: 'YYYY', month: 'YYYY-MM', day: 'YYYY-MM-DD' }[c.precision || 'day']; if (!pattern) throw Error('日期精度应为 year、month 或 day') }
 else if (style === 'datetime' || style === 'time') { pattern = { minute: style === 'datetime' ? 'YYYY-MM-DD HH:mm' : 'HH:mm', second: style === 'datetime' ? 'YYYY-MM-DD HH:mm:ss' : 'HH:mm:ss' }[c.precision || 'second']; if (!pattern) throw Error('时间精度应为 minute 或 second') }
 else pattern = { short: 'YYYY-MM-DD HH:mm' }[style] || 'YYYY-MM-DD';
 return pattern.replace(/YYYY|MM|DD|HH|mm|ss/g, t => parts[{ YYYY: 'year', MM: 'month', DD: 'day', HH: 'hour', mm: 'minute', ss: 'second' }[t]]);
}

function fmtElement(v, c) { // 数组元素：深度限 1，仅 elementType 对应标量应用 elementFormat
 if (v === null || v === undefined) return EMPTY(c);
 const et = c.elementType, ef = c.elementFormat;
 if (ef && typeof ef === 'object') {
  if (et === 'number' && typeof v === 'number' && Number.isFinite(v)) return fmtNumber(v, ef);
  if (et === 'boolean' && typeof v === 'boolean') return fmtBoolean(v, ef);
  if (et === 'string' && typeof v === 'string') return fmtString(v, ef);
  if (et === 'date' && typeof v === 'string') return fmtDate(v, ef);
  if (et === 'time' && typeof v === 'string') return fmtTime(v, ef);
 }
 return scalarText(v, c);
}

function fmtArray(value, c) {
 if (!Array.isArray(value)) throw Error('请输入 JSON 数组');
 if (c.style === 'count') return String(value.length);
 if (c.style === 'json') return JSON.stringify(value, null, 2);
 const max = c.maxItems ?? 10;
 if (!Number.isInteger(max) || max < 1 || max > 100) throw Error('最多显示项数须为1—100的整数');
 const rows = value.slice(0, max).map(v => fmtElement(v, c)); // 只截显示，不动真实数组
 if (value.length > max) rows.push(`…共${value.length}项，已显示前${max}项`);
 return rows.join(c.style === 'list' ? '\n' : (c.separator ?? '、'));
}

function fmtStruct(value, c) {
 if (typeof value !== 'object' || Array.isArray(value)) throw Error('请输入 JSON 对象');
 if (c.style === 'compact') return JSON.stringify(value, null, 0);
 if (c.style === 'template') // 仅顶层 {字段}；含 "." 的路径原样保留；缺失字段用空值占位
  return String(c.template ?? '').replace(/\{([^{}]+)\}/g, (m, name) => name.includes('.') ? m : (Object.prototype.hasOwnProperty.call(value, name) ? scalarText(value[name], c) : EMPTY(c)));
 if (c.style === 'pairs') {
  let entries = Object.entries(value);
  if (Array.isArray(c.fields)) { const has = new Set(Object.keys(value)); entries = c.fields.filter(f => typeof f === 'string' && has.has(f)).map(f => [f, value[f]]) } // fields 顺序，缺失键跳过
  return entries.map(([k, v]) => `${k}: ${scalarText(v, c)}`).join(c.separator ?? '；');
 }
 return JSON.stringify(value, null, 2); // 历史 json 样式与未指定样式
}

function seriesValue(v, vf, c) { // 序列观测值：按值形态路由到对应类型格式（string/number/boolean/date/time）
 if (v === null || v === undefined) return vf.emptyText ?? EMPTY(c);
 if (typeof v === 'number') return fmtNumber(v, vf);
 if (typeof v === 'boolean') return fmtBoolean(v, vf);
 if (typeof v === 'string') {
  if (/^\d{4}-\d{2}-\d{2}$/.test(v)) return fmtDate(v, vf);
  if (/(Z|[+-]\d{2}:?\d{2})$/.test(v) && Number.isFinite(new Date(v).getTime())) return fmtTime(v, vf);
  return fmtString(v, vf);
 }
 return JSON.stringify(v);
}

function fmtTimeSeries(value, c) {
 if (!Array.isArray(value)) throw Error('请输入 JSON 数组');
 if (value.length === 0) return '暂无记录';
 const tf = c.timeFormat && typeof c.timeFormat === 'object' ? c.timeFormat : {}, vf = c.valueFormat && typeof c.valueFormat === 'object' ? c.valueFormat : {};
 return value.map(item => {
  if (item === null || typeof item !== 'object' || Array.isArray(item) || !('timestamp' in item) || !('value' in item)) throw Error('序列记录须含 timestamp 和 value');
  if (item.timestamp === null || item.timestamp === undefined) return EMPTY(c);
  return `${fmtTime(item.timestamp, tf)}  ${seriesValue(item.value, vf, c)}`; // 时间与值之间两个空格
 }).join('\n');
}

let input = ''; process.stdin.setEncoding('utf8'); process.stdin.on('data', c => input += c); process.stdin.on('end', () => {
 try {
 const parsed = JSON.parse(input), value = parsed.value, type = parsed.type, c = parsed.config && typeof parsed.config === 'object' ? parsed.config : {};
 let out;
 if (c.mode === 'code') {
  const context = vm.createContext(Object.create(null), { codeGeneration: { strings: false, wasm: false } });
  out = vm.runInContext(`"use strict";const value=JSON.parse(${JSON.stringify(JSON.stringify(value))});const result=(function(value){${c.code || ''}\n})(value);if(typeof result!=="string")throw Error("函数必须返回文本");result;`, context, { timeout: 200 });
 } else if (value === null || value === undefined) out = EMPTY(c); // 仅 null/undefined 算空值
 else if (type === 'number') out = fmtNumber(value, c);
 else if (type === 'boolean') out = fmtBoolean(value, c);
 else if (type === 'date') out = fmtDate(value, c);
 else if (type === 'time') out = fmtTime(value, c);
 else if (type === 'array') out = fmtArray(value, c);
 else if (type === 'struct') out = fmtStruct(value, c);
 else if (type === 'timeSeries') out = fmtTimeSeries(value, c);
 else out = fmtString(value, c);
 if (typeof out !== 'string' || out.length > 10000) throw Error('输出必须是最多10000字符的文本');
 process.stdout.write(JSON.stringify({ formatted: out }));
 } catch (e) { process.stdout.write(JSON.stringify({ error: e.message })); process.exitCode = 1 }
});
