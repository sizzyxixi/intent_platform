// 分类树工具：叶子选项、路径显示
export type IntentRef = { code: string; name: string; status: string }
export type CatNode = {
  id: number
  code: string | null
  name: string
  depth: number
  count: number
  description?: string
  enabled?: boolean
  children: CatNode[]
  intents?: IntentRef[]
  has_children?: boolean
  child_count?: number
}
export type Tax = { levels: string[]; category_levels: number; tree: CatNode[]; unassigned: number }
export type Leaf = { id: number; label: string; root: CatNode; names: string[]; descs: string[] }

export function leaves(tax: Tax): Leaf[] {
  const out: Leaf[] = []
  const walk = (n: CatNode, root: CatNode, names: string[], descs: string[]) => {
    const nm = [...names, n.name]
    const ds = [...descs, n.description || '']
    if (n.depth === tax.category_levels) out.push({ id: n.id, label: nm.join(' / '), root, names: nm, descs: ds })
    else (n.children || []).forEach(c => walk(c, root, nm, ds))
  }
  tax.tree.forEach(r => walk(r, r, [], []))
  return out
}

export const pathText = (p: { name: string }[] | undefined) => (p || []).map(x => x.name).join(' / ')


export function toCascade(nodes: CatNode[], stopDepth?: number): import('./picker').CascadeNode[] {
  return (nodes || []).map(n => ({
    id: n.id,
    name: n.name,
    description: n.description || '',
    depth: n.depth,
    disabled: n.enabled === false,
    children: stopDepth && n.depth >= stopDepth ? undefined
      : (n.children?.length ? toCascade(n.children, stopDepth) : undefined),
  }))
}

export function withoutSubtree(nodes: CatNode[], banId: number): CatNode[] {
  return (nodes || []).filter(n => n.id !== banId).map(n => ({
    ...n, children: withoutSubtree(n.children || [], banId),
  }))
}
