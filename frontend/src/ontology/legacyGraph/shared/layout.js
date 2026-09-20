// 节点尺寸计算（编辑页与预览页共用）
export function nodeW(name) {
  const l = [...(name || '')].length
  return Math.min(205, Math.max(112, 76 + l * 9.2))
}
export function nodeH(name) {
  return (name || '').length > 12 ? 46 : 40
}
