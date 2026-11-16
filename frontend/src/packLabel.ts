/** 命令包展示名：截到第一个中英文逗号，去掉「包含…能力」这类说明。列表、筛选、悬停提示都用这个短名称。 */
export function shortPackName(name?: string | null, fallback = ''): string {
  const raw = (name || '').trim()
  if (!raw) return fallback
  const cut = raw.search(/[,，]/)
  return (cut >= 0 ? raw.slice(0, cut) : raw).trim() || fallback
}
